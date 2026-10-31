"""Read the General Fund detailed revenue and expenditure schedules (budget and actual) from each Boone County audit.

Layouts: FY2015-17 and FY2024-25 print "Original and Final Budget | Actual | Variance Over (Under)" on separate revenue and
expenditure schedules with labeled totals; FY2018-20 print the revenue schedule with unlabeled subtotals (a numbers-only line
closes the open group); FY2021-23 print one combined "001 General Fund" schedule with unlabeled subtotals. The county does not
amend its budget, so "final budget" is also the original.

Output data/boone_county_il/extracted/audits/<doc>_gf_schedule.csv:
  doc_id, pdf_page, schedule (revenue | expenditure), row_seq, path, label, line_type (item | total), budget, actual, variance
and <doc>_gf_schedule_checks.csv (check, ok, detail). Checks: variance = actual - budget on every line; every group total
equals its items plus its subgroups; TOTAL REVENUES / TOTAL EXPENDITURES equal the audit's governmental-funds statement General
column (extract_audit_fund_statements.py). Overrides: data/boone_county_il/overrides/audit_gf_<doc>.csv (kind allow_diff).
Usage: python3 pipelines/boone_county_il/extract_audit_gf_schedules.py FY2025 [...]
"""
import csv, re, subprocess, sys
from pathlib import Path

from extract_audit_fund_statements import parse_line, place

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "sources/boone_county_il/audits"
OUT = ROOT / "data/boone_county_il/extracted/audits"
OVR = ROOT / "data/boone_county_il/overrides"

BOILER = re.compile(r"BOONE COUNTY|SCHEDULE OF|^\s*GENERAL FUND|^\s*001 GENERAL FUND|FOR THE YEAR ENDED|YEAR ENDED|ORIGINAL AND|FINAL\s*$|"
                    r"VARIANCE|^\s*BUDGET\b|^\s*ACTUAL|CONTINUED|SEE INDEPENDENT|\(SEE |SOURCES \(USES\)|^\s*-\s*\d+\s*-\s*$|FINAL BUDGET|"
                    r"OVER \(UNDER\)", re.I)


def norm(s):
    s = re.sub(r"(?i)\btotal\b", "", s).lower()
    s = re.sub(r"[^a-z]", "", s)
    return s.removesuffix("s")


def schedule_pages(pages):
    """Contiguous block of pages holding the General Fund revenue / expenditure schedules."""
    hits = []
    for i, p in enumerate(pages, 1):
        U = re.sub(r"\s+", " ", p.upper())[:300]
        if re.search(r"SCHEDULE OF (DETAILED )?(REVENUES|EXPENDITURES)", U) and "GENERAL FUND" in U and "COMBINING" not in U \
                and "BY SOURCE" in U or re.search(r"SCHEDULE OF (REVENUES|EXPENDITURES)( AND OTHER FINANCING SOURCES \(USES\))? - BUDGET AND ACTUAL", U) \
                and "GENERAL FUND" in U and "COMBINING" not in U:
            hits.append(i)
        elif re.search(r"SCHEDULE OF REVENUES BY SOURCE, EXPENDITURES", U) and "001 GENERAL FUND" in U:
            hits.append(i)
    # drop the condensed budgetary comparison in the required supplementary information (a lone early page)
    block = [h for h in hits if any(abs(h - o) == 1 for o in hits)] or hits
    return block


def read_lines(pages, nums):
    rows = []
    for pn in nums:
        for line in pages[pn - 1].split("\n"):
            if not line.strip() or re.match(r"^\s*-\s*\d+\s*-\s*$", line) or re.search(r"(?i)see independent auditor|\(see independent|\(this schedule is continued", line):
                continue
            label, vals = parse_line(line)
            if len(vals) == 1 and vals[0][0] == 0 and label and line.rstrip().endswith(("-", "–")) and not re.search(r"\d", line):
                vals = []  # a heading that ends in a dash ("County farm -")
            if BOILER.search(label) and not re.match(r"(?i)\s*total", label):
                continue
            if len(vals) == 0 and re.search(r"BOONE COUNTY|Independent Auditor", line, re.I):
                continue
            if len(vals) == 1 and not label:  # a lone page number
                continue
            if len(vals) == 0 and not BOILER.search(label) and re.fullmatch(r"[\d\s-]*", label):
                continue
            rows.append(dict(pdf_page=pn, label=label, vals=vals))
    return rows


def parse(doc, pages, nums):
    raw = read_lines(pages, nums)
    # continuation labels: a heading-like line followed by nothing is a heading; a numbers-line with a label is an item
    numbered = [r for r in raw if r["vals"]]
    for r in numbered:
        pass
    full = [r for r in raw if len(r["vals"]) == 3]
    edges = [sorted(r["vals"][c][1] for r in full)[len(full) // 2] for c in range(3)]
    for r in raw:
        if not r["vals"]:
            continue
        if len(r["vals"]) == 3:
            r["flag"] = ""
            r["vals"] = [v for v, _ in r["vals"]]
        elif len(r["vals"]) < 3:  # blank cells: place each number in the column with the nearest right edge
            cells, given = [0, 0, 0], set()
            for v, e in r["vals"]:
                c = min(range(3), key=lambda c: abs(edges[c] - e))
                cells[c] = v
                given.add(c)
            r["vals"], r["flag"] = cells, ""
            r["placed"] = given
        else:
            r["flag"] = "too_many"
    return raw


def main(fy):
    doc = f"{fy}_audit"
    text = subprocess.run(["pdftotext", "-layout", str(SRC / f"{doc}.pdf"), "-"], capture_output=True, text=True, check=True).stdout
    pages = text.split("\f")
    nums = schedule_pages(pages)
    raw = parse(doc, pages, nums)
    # edges for short rows
    full = [r for r in raw if r["vals"] and r.get("flag") == ""]
    out, checks = [], []
    stack = []  # [name, norm, sum_budget, sum_actual, sum_var]
    schedule = None
    done_root = False
    seq = 0

    def add_check(name, ok, detail=""):
        checks.append((name, ok, detail))

    allowed = {}
    p = OVR / f"audit_gf_{doc}.csv"
    if p.exists():
        for o in csv.DictReader(open(p, newline="")):
            allowed[(o["path"], o["label"], o["column"])] = int(o["value"])

    def close(group, vals, label, path, page):
        sums = group[2:]
        for k, col in enumerate(("budget", "actual", "variance")):
            if sums[k] != vals[k]:
                diff = sums[k] - vals[k]
                if allowed.get((path, label, col)) == diff:
                    add_check(f"accepted difference: {label} {col}", True, f"diff {diff}")
                else:
                    add_check(f"group total: {label} {col} (p{page})", False, f"lines {sums[k]} != printed {vals[k]} (path={path!r}, label={label!r}, diff={diff})")
        add_check(f"group total: {label}", True) if all(sums[k] == vals[k] for k in range(3)) else None

    last_label_total = None
    items = [r for r in raw if r["vals"] and r.get("flag") == "" and r["label"] and not re.match(r"(?i)\s*total\b", r["label"])]
    # FY2021-23 print the variance as budget - actual ("Variance with Final Budget" favorable positive), the others as actual - budget
    sched_of, cur = {}, None
    for r in raw:
        if not r["vals"] and re.fullmatch(r"(?i)revenues:?|expenditures:?", r["label"]):
            cur = r["label"].rstrip(":").lower()
        sched_of[id(r)] = cur
    signs = {}
    for sc in ("revenues", "expenditures"):
        its = [r for r in items if sched_of[id(r)] == sc]
        signs[sc] = 1 if sum(r["vals"][2] == r["vals"][1] - r["vals"][0] for r in its) >= sum(r["vals"][2] == r["vals"][0] - r["vals"][1] for r in its) else -1
    for r in raw:
        pn, label, vals = r["pdf_page"], r["label"], r["vals"]
        U = label.upper()
        if r.get("placed") is not None and 2 not in r["placed"] and r["vals"]:
            sc = "revenues" if schedule == "revenue" else "expenditures"
            vals[2] = signs[sc] * (vals[1] - vals[0])  # a blank variance cell: the printed totals include the amount
        if not vals:
            if done_root:
                continue
            if re.fullmatch(r"REVENUES:?", U):
                schedule = "revenue"
                stack = [["REVENUES", norm("REVENUES"), 0, 0, 0]]
                continue
            if re.fullmatch(r"EXPENDITURES:?", U):
                schedule = "expenditure"
                stack = [["EXPENDITURES", norm("EXPENDITURES"), 0, 0, 0]]
                continue
            if stack:
                stack.append([label.rstrip(":"), norm(label), 0, 0, 0])
            continue
        if r.get("flag") != "":
            add_check(f"line shape: {label} has {len(vals)} numbers", False, f"p{pn}")
            continue
        seq += 1
        path = "|".join(g[0] for g in stack[1:])
        is_total = bool(re.match(r"(?i)\s*total\b", label))
        if not stack:  # numbers before any root heading
            continue
        unlabeled_item = False
        if not label and stack and [stack[-1][2], stack[-1][3], stack[-1][4]] != list(vals) and len(stack) > 1:
            label, unlabeled_item = "(unlabeled line)", True  # a line item printed without its label, not a subtotal
        if (not is_total and label) or unlabeled_item:
            if signs[('revenues' if schedule == 'revenue' else 'expenditures')] * (vals[1] - vals[0]) != vals[2]:
                add_check(f"variance = actual - budget: {label} (p{pn})", False, f"{vals}")
            for g in stack:
                g[2] += vals[0]; g[3] += vals[1]; g[4] += vals[2]
            # an item counts toward every open group, so subtotals later subtract nothing: closed groups are removed below
            out.append(dict(doc_id=doc, pdf_page=pn, schedule=schedule, row_seq=seq, path=path, label=label, line_type="item",
                            budget=vals[0], actual=vals[1], variance=vals[2]))
            continue
        # a total: labeled or numbers-only (closes the innermost open group)
        if is_total:
            key = norm(label)
            idx = next((i for i in range(len(stack) - 1, -1, -1) if stack[i][1] == key or
                        (len(key) > 6 and (stack[i][1].startswith(key) or key.startswith(stack[i][1])))), None)
            if idx is None:
                add_check(f"total without open group: {label} (p{pn})", False, "")
                continue
        else:
            idx = len(stack) - 1
            label = f"(subtotal of {stack[idx][0]})"
        group = stack[idx]
        exp_var = signs[('revenues' if schedule == 'revenue' else 'expenditures')] * (vals[1] - vals[0])
        if exp_var != vals[2]:
            if allowed.get((path if is_total and idx == 0 else path, label, "line_variance")) == vals[2] - exp_var:
                add_check(f"accepted difference: variance of {label}", True, f"printed {vals[2]} expected {exp_var}")
            else:
                add_check(f"variance = actual - budget: {label} (p{pn})", False, f"{vals} (path={path!r}, label={label!r}, diff={vals[2] - exp_var})")
        close(group, vals, group[0] if not is_total else label, path, pn)
        out.append(dict(doc_id=doc, pdf_page=pn, schedule=schedule, row_seq=seq, path="|".join(g[0] for g in stack[1:idx]),
                        label=label, line_type="total", budget=vals[0], actual=vals[1], variance=vals[2]))
        del stack[idx:]  # the group (and any unclosed groups inside it) are closed; items already counted in the parents
        if idx == 0:
            out[-1]["path"] = ""
            done_root = True
            last_label_total = (schedule, vals)
            checks.append((f"total {schedule}", True, f"budget {vals[0]} actual {vals[1]}"))
            done_root = False
            stack = []
    # tie to the governmental funds statement General column
    st = AUD_STATEMENT(doc)
    for sched, vals in [(o["schedule"], (o["budget"], o["actual"])) for o in out if o["line_type"] == "total" and o["path"] == ""
                        and o["label"].upper().startswith("TOTAL REVENUES") or False]:
        pass
    for o in out:
        if o["line_type"] == "total" and o["path"] == "" and re.match(r"(?i)total (revenues|expenditures)$", o["label"].strip()):
            want = st.get("total revenues" if "REVENUES" in o["label"].upper() else "total expenditures")
            if want is not None:
                # the statement's General Fund consolidates subfunds the legally budgeted fund 001 schedule does not carry
                # FY2015-FY2019 the two agree to the dollar; from FY2020 the statement's General column includes subfunds
                strict = int(doc[2:6]) <= 2019
                add_check(f"{o['label']} actual {'=' if strict else 'vs'} governmental funds statement General column", o["actual"] == want or not strict,
                          f"schedule {o['actual']} statement {want} difference {want - o['actual']}")
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / f"{doc}_gf_schedule.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["doc_id", "pdf_page", "schedule", "row_seq", "path", "label", "line_type", "budget", "actual", "variance"])
        w.writeheader()
        w.writerows(out)
    with open(OUT / f"{doc}_gf_schedule_checks.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["check", "ok", "detail"])
        w.writerows(checks)
    bad = [c for c in checks if not c[1]]
    print(f"{doc}: pages {nums[0]}-{nums[-1]} ({len(nums)}), {len(out)} lines, {len(checks)} checks, {len(bad)} failed")
    for c in bad[:25]:
        print("   FAIL", c[0], c[2])


def AUD_STATEMENT(doc):
    """General column of the governmental funds statement: total revenues and total expenditures."""
    p = OUT / f"{doc}_fund_statements.csv"
    res = {}
    if not p.exists():
        return res
    for r in csv.DictReader(open(p, newline="")):
        if r["statement"] == "changes" and r["col"] == "General":
            l = r["label"].lower().strip()
            if l == "total revenues" or l == "total expenditures":
                res[l] = int(r["amount"])
    return res


if __name__ == "__main__":
    for a in sys.argv[1:]:
        main(a)
