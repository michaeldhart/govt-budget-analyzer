"""Extract the lease (GASB 87) and subscription-based IT arrangement (SBITA, GASB 96) notes of a Belvidere Park District audit.

Usage: python3 pipelines/belvidere_park_district/extract_audit_leases.py FY2025

Reads (text layer; the scanned FY2020-FY2022 audits through the cached 200 dpi OCR of their note pages): the Leases note and the Subscription-Based
Information Technology Agreements note: terms (agreement dates, term, monthly payment, discount rates, amortization expense), the remaining obligations
(principal, interest, total by year) and the right-to-use asset roll-forward (beginning, additions, deletions, ending) per agreement.
Only the FY2024 and FY2025 audits have such notes (the district recognised its first subscription in 2024 and its first lease in 2025); for the other
five audits the script records that the note pages have no lease or subscription text and writes only the checks file.
Output (data/belvidere_park_district/extracted/audits/): <doc>_lease_schedule.csv, _lease_assets.csv, _lease_terms.csv, _lease_checks.csv.
Validation (all must hold, or the script fails and writes nothing): every schedule row's total = principal + interest and the Total row adds; the schedule's
principal equals the liability the note states, the debt note's SBITA balance (extracted by extract_audit_debt.py) and, year by year, the debt note's own
SBITA schedule; the first year's principal equals the debt note's current portion; each asset roll-forward adds (beginning + additions + deletions = ending)
and its ending equals the Statement of Net Position's right-of-use line and the reconciliation of the governmental funds; the SBITA additions equal the
debt activity's new issues and the amortization its retirements; the lease liability at inception equals the lease proceeds in the statement of changes
and the principal repaid equals the reconciliation's lease obligation line; the annual lease payment equals twelve monthly payments.
"""
import csv
import os
import re
import subprocess
import sys
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import extract_budget_report as R  # noqa: E402
import extract_audit_imrf as I  # noqa: E402

ENTITY = R.ENTITY
NOTE_PAGES = list(range(27, 36))


def get_pages(pdf, doc):
    t = subprocess.run(["pdftotext", "-layout", pdf, "-"], capture_output=True, text=True, check=True).stdout.split("\f")
    if sum(len(p) for p in t) > 800 * len(t):
        return t
    n = len(t) - (1 if t and not t[-1].strip() else 0)
    o = R.ocr_selected(pdf, doc, [i for i in NOTE_PAGES if i <= n], dpi=200)
    return [o.get(i, "") for i in range(1, n + 1)]


def money(s):
    return Decimal(s.replace(",", "").replace("$", ""))


def schedule_rows(lines):
    """(year_ending, [principal, interest, total]) per `2026 $ 17,080 $ 3,235 $ 20,315` line and the Total line."""
    out = []
    for raw in lines:
        m = re.match(r"^\s*(\d{4}(?:-\d{4})?|Total)\s+(.*)$", raw)
        if not m:
            continue
        label, vals = I.tokens_row("x " + m.group(2))
        if len(vals) == 3:
            out.append((m.group(1), vals))
    return out


def asset_rows(lines):
    """(asset, [beginning, additions, deletions, ending]) for the lines between the roll-forward's heading and its Total."""
    out = []
    for raw in lines:
        label, vals = I.tokens_row(raw.strip())
        if len(vals) == 4 and label:
            out.append((label, vals))
            if label == "Total":
                break
    return out


SECTION_PAGES = []  # PDF page of each line returned by the last section() call


def section(pages, start_rx, end_rx):
    """Text and first PDF page of the note between two headings (heading at the start of a line)."""
    text, first, SECTION_PAGES[:] = [], None, []
    on = False
    for i, p in enumerate(pages, 1):
        for raw in p.splitlines():
            if not on and re.match(start_rx, raw):
                on, first = True, i
            elif on and re.match(end_rx, raw):
                return "\n".join(text), first
            if on:
                text.append(raw)
                SECTION_PAGES.append(i)
    return ("\n".join(text), first) if on else ("", None)


def page_of(pages, text_rx, lines_from=None):
    return next((i for i, p in enumerate(pages, 1) if re.search(text_rx, p)), "")


def parse(pages):
    lease, lp = section(pages, r"^\s*\d+\.\s+LEASES\s*$", r"^\s*\d+\.\s+(SUBSCRIPTION|EMPLOYEE)")
    lease_pages = list(SECTION_PAGES)
    sbita, sp = section(pages, r"^\s*\d+\.\s+SUBSCRIPTION-BASED", r"^\s*\d+\.\s+EMPLOYEE")
    out = {"lease": None, "sbita": None}
    for kind, text, pg in (("lease", lease, lp), ("sbita", sbita, sp)):
        if not text:
            continue
        flat = " ".join(text.split())
        lines = text.splitlines()
        pg_of = list(SECTION_PAGES) if kind == "sbita" else lease_pages
        sched_i = next(i for i, l in enumerate(lines) if re.search(r"Year Ending", l))
        asset_i = next(i for i, l in enumerate(lines) if re.search(r"right-to-use assets", l, re.I) and not re.search(r"amortized", l))
        pgs = {"schedule": pg_of[sched_i], "assets": pg_of[asset_i]}
        out[kind] = dict(flat=flat, sched=schedule_rows(lines[sched_i:asset_i]), assets=asset_rows(lines[asset_i + 1:]), pages=pgs, note_page=pg)
    return out


def terms(kind, d):
    """[(agreement, term, value, value_text, unit)] from the note's prose."""
    f = d["flat"]
    out = []

    def add(agr, term, value=None, text="", unit=""):
        out.append((agr, term, "" if value is None else value, text, unit))
    if kind == "lease":
        m = re.search(r"entered into a (\d+)-month lease agreement for ([\w ]+?)\. The lease terminates in (\w+ \d{4})\. Under the terms of the lease, the District makes monthly payments of \$([\d,.]+)\. "
                      r"At December 31, \d{4}, the District recognized a right-of-\s*use asset of \$([\d,]+) and a lease liability of \$([\d,]+) related to this agreement\. "
                      r"The District used an incremental discount rate of ([\d.]+)%", f)
        agr = m.group(2).capitalize()
        add(agr, "term_months", int(m.group(1)), unit="months")
        add(agr, "termination", None, m.group(3))
        add(agr, "monthly_payment", money(m.group(4)), unit="usd")
        add(agr, "right_of_use_asset", money(m.group(5)), unit="usd")
        add(agr, "lease_liability", money(m.group(6)), unit="usd")
        add(agr, "discount_rate", Decimal(m.group(7)), unit="percent")
        return out
    ag = re.findall(r"signed a (\d+)-year (?:subscription-based information technology agreement \(SBITA\)|SBITA) with (.+?) applicable from (\w+ \d{4}) through (\w+ \d{4})", f)
    rates = re.search(r"incremental discount rates? of ([\d.]+)%(?: and ([\d.]+)%)?", f)
    rates = [r for r in rates.groups() if r]
    assert len(rates) == len(ag), "discount rates do not match the agreements"
    for (yrs, vendor, start, end), rate in zip(ag, rates):
        add(vendor, "term_years", int(yrs), unit="years")
        add(vendor, "start", None, start)
        add(vendor, "end", None, end)
        add(vendor, "discount_rate", Decimal(rate), unit="percent")
    m = re.search(r"recognized (?:a )?right-to-use subscription assets? and (?:a )?subscription liabilit(?:y|ies) of \$([\d,]+) each", f)
    add("All agreements", "right_of_use_asset", money(m.group(1)), unit="usd")
    add("All agreements", "subscription_liability", money(m.group(1)), unit="usd")
    m = re.search(r"recorded \$([\d,]+) in amortization expense", f)
    add("All agreements", "amortization_expense", money(m.group(1)), unit="usd")
    return out


def find_one(text, rx):
    m = re.search(rx, text, re.M)
    return None if m is None else [money(g) for g in m.groups() if g]


def check(ent, fy, doc, pages, parsed):
    out = []

    def add(name, expected, found, tol=0):
        out.append((doc, name, expected, found, "ok" if expected == found else ("ok: rounding" if expected is not None and found is not None and abs(expected - found) <= tol else "FAIL")))
    full = "\n".join(pages)
    snp_sbita = find_one(full, r"^\s*SBITA right-of-use asset\s+([\d,]+)\s*$")
    rec = find_one(full, r"^\s*SBITA right-of-use asset\s+([\d,]+)\s+([\d,]+)\s*$")
    sbita_liab_rec = find_one(full, r"^\s*SBITA liability\s+\(([\d,]+)\)\s*$")
    ext = ent.data_path("extracted", "audits")

    def rdx(name):
        p = os.path.join(ext, f"{doc}_{name}.csv")
        return list(csv.DictReader(open(p))) if os.path.exists(p) else []
    out_ = {(r["instrument_id"], r["column"]): Decimal(r["amount"]) for r in rdx("debt_outstanding")}
    act = {(r["instrument_id"], r["column"]): Decimal(r["amount"]) for r in rdx("debt_activity")}
    for kind in ("lease", "sbita"):
        d = parsed[kind]
        if not d:
            continue
        sched = d["sched"]
        body = [(l, v) for l, v in sched if l != "Total"]
        tot = next(v for l, v in sched if l == "Total")
        for l, v in body:
            add(f"{kind} {l}: total = principal + interest", v[2], v[0] + v[1], tol=1 if kind == "lease" else 0)
        for j, nm in enumerate(("principal", "interest", "total")):
            add(f"{kind} schedule: total {nm}", tot[j], sum(v[j] for _, v in body), tol=1)
        tw = {t: (v, x) for a, t, v, x, u in terms(kind, d)} if kind == "lease" else {}
        assets = d["assets"]
        for l, v in assets:
            add(f"{kind} asset {l}: beginning + additions + deletions = ending", v[3], v[0] + v[1] + v[2])
        arows = [v for l, v in assets if l != "Total"]
        atot = next(v for l, v in assets if l == "Total")
        for j, nm in enumerate(("beginning", "additions", "deletions", "ending")):
            add(f"{kind} assets: total {nm}", atot[j], sum(v[j] for v in arows))
        if kind == "sbita":
            liab = terms(kind, d)[-2][2]  # "All agreements" subscription_liability
            add("sbita: liability in the note = asset ending", liab, atot[3])
            add("sbita: schedule principal = liability", tot[0], liab)
            add("sbita: schedule principal = debt note SBITA balance", out_.get(("sbita", "balance")), tot[0])
            add("sbita: first year's principal = debt note current portion", out_.get(("sbita", "current_portion")), body[0][1][0])
            add("sbita: asset ending = Statement of Net Position right-of-use line", snp_sbita[0], atot[3])
            add("sbita: asset ending = reconciliation line", rec[0], atot[3])
            add("sbita: liability = reconciliation line", sbita_liab_rec[0], tot[0])
            add("sbita: additions = debt activity new issues", act.get(("sbita", "new_issues")), atot[1])
            add("sbita: deletions = debt activity retired (amortization)", act.get(("sbita", "retired")), -atot[2])
            add("sbita: amortization expense in the note = deletions", terms(kind, d)[-1][2], -atot[2])
            add("sbita: asset beginning = debt activity beginning", act.get(("sbita", "beginning")), atot[0])
            ds = {r["period"]: Decimal(r["amount"]) for r in rdx("debt_schedule") if r["instrument_id"] == "sbita" and r["kind"] == "principal"}
            di = {r["period"]: Decimal(r["amount"]) for r in rdx("debt_schedule") if r["instrument_id"] == "sbita" and r["kind"] == "interest"}
            for l, v in body:
                add(f"sbita {l}: principal = debt note schedule", ds.get(l, Decimal(0)), v[0])
                add(f"sbita {l}: interest = debt note schedule", di.get(l, Decimal(0)), v[1])
        else:
            liab = tw["lease_liability"][0]
            add("lease: schedule principal = liability in the note", liab, tot[0])
            add("lease: asset ending = right-of-use asset in the note", tw["right_of_use_asset"][0], atot[3])
            cap = find_one(full, r"^\s*Leased equipment\s+-\s+([\d,]+)\s+-\s+([\d,]+)\s*$")
            add("lease: asset ending = capital asset note's leased equipment", cap[1], atot[3])
            snp = find_one(full, r"^\s*Right-of-use leased assets\s+([\d,]+)\s*$")
            add("lease: asset ending = Statement of Net Position right-of-use line", snp[0], atot[3])
            rep = find_one(full, r"^\s*Lease obligation\s+([\d,]+)\s+[\d,]+\s*$")
            iss = find_one(full, r"^\s*Lease obligation\s+\(([\d,]+)\)\s+\([\d,]+\)\s*$")
            lea = find_one(full, r"^\s*Lease obligation\s+\(([\d,]+)\)\s*$")
            add("lease: liability = reconciliation line", lea[0], tot[0])
            add("lease: liability at inception = lease proceeds in the reconciliation", iss[0], atot[1])
            add("lease: principal repaid in the reconciliation = inception - ending liability", rep[0], iss[0] - tot[0])
            add("lease: lease proceeds in the statement of changes = additions",
                find_one(full, r"^\s*Lease proceeds\s+[\d,]+\s+([\d,]+)\s*$")[0], atot[1])
            monthly = tw["monthly_payment"][0]
            for l, v in body:
                if l.isdigit() and v[2] > 3 * monthly:
                    add(f"lease {l}: annual payment = twelve monthly payments", (monthly * 12).quantize(Decimal(1)), v[2], tol=1)
            months = tw["term_months"][0]
            add("lease: remaining payments = (term months - 1) x monthly payment", (monthly * (months - 1)).quantize(Decimal(1)), tot[2], tol=1)
    return out


def write(ent, fy, doc, parsed, checks):
    out = ent.data_path("extracted", "audits")
    os.makedirs(out, exist_ok=True)

    def w(name, header, rows_):
        with open(os.path.join(out, f"{doc}_{name}.csv"), "w", newline="") as f:
            wr = csv.writer(f)
            wr.writerow(header)
            wr.writerows(rows_)
    kinds = [k for k in ("lease", "sbita") if parsed[k]]
    if kinds:
        w("lease_schedule", ["fiscal_year", "doc", "kind", "year_ending", "principal", "interest", "total", "row_kind", "pdf_page"],
          [(fy, doc, k, l, *v, "total" if l == "Total" else "range" if "-" in l else "year", parsed[k]["pages"]["schedule"]) for k in kinds for l, v in parsed[k]["sched"]])
        w("lease_assets", ["fiscal_year", "doc", "kind", "asset", "measure", "amount", "pdf_page"],
          [(fy, doc, k, l, nm, v[j], parsed[k]["pages"]["assets"]) for k in kinds for l, v in parsed[k]["assets"] for j, nm in enumerate(("beginning", "additions", "deletions", "ending"))])
        w("lease_terms", ["fiscal_year", "doc", "kind", "agreement", "term", "value", "value_text", "unit", "pdf_page"],
          [(fy, doc, k, *t, parsed[k]["note_page"]) for k in kinds for t in terms(k, parsed[k])])
    w("lease_checks", ["doc", "check", "expected", "found", "result"], checks or [(doc, "no lease or subscription note in the audit's note pages", 0, 0, "ok")])


def main(argv):
    doc = argv[1] if "_" in argv[1] else f"{argv[1].upper()}_audit"
    fy = int(doc[2:6])
    ent = R.Entity.load(ENTITY)
    src = {r["doc_id"]: r for r in csv.DictReader(open(ent.config_path("source_urls.csv")))}
    pages = get_pages(ent.source_path(src[doc]["pdf_path"]), doc)
    parsed = parse(pages)
    checks = check(ent, fy, doc, pages, parsed) if any(parsed.values()) else []
    fails = [c for c in checks if c[4] == "FAIL"]
    print(f"{doc}: lease note {'yes' if parsed['lease'] else 'no'}, SBITA note {'yes' if parsed['sbita'] else 'no'}, {len(checks)} checks, {len(fails)} failures")
    for c in fails:
        print("  FAIL", c[1], "expected", c[2], "found", c[3])
    if fails:
        sys.exit(1)
    write(ent, fy, doc, parsed, checks)


if __name__ == "__main__":
    main(sys.argv)
