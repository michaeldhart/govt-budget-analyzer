"""Levy ordinance (FY N) vs the county's tax extension for tax year N-1, fund by fund (tax years 2021-2024).

Reads ordinance_lines.csv (section levy) and assessed_valuations.csv (the audits' Assessed Valuations schedule); writes
data/belvidere_park_district/levy_vs_extension.csv. Fund names are mapped through entities/belvidere_park_district/levy_extension_funds.csv.
One row per levy ordinance x fund, plus a Total row (the sum of the fund rows, the audit's printed Total beside it).
"""
import csv
import re
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENT = "belvidere_park_district"
D = ROOT / "data" / ENT
LEVY_DOCS = ["FY2022_levy", "FY2023_levy", "FY2024_levy", "FY2025_levy"]
COLUMNS = ["entity_id", "doc_id", "period_fy", "tax_year", "fund_key", "fund", "levy", "extension", "difference", "difference_pct", "collected",
           "percent_collected", "valuation", "rate_computed", "rate_printed", "rate_diff", "extension_total_printed", "exception_ids", "explanation"]


def read(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def audit_value(av, tax_year, section, matcher):
    """The first audit reporting this tax year (later audits repeat the figure; checked in tests)."""
    hits = [r for r in av if r["tax_year"] == str(tax_year) and r["section"] == section and matcher(r["line_item"])]
    assert len(hits) >= 1, (tax_year, section)
    first = min(hits, key=lambda r: r["period_fy"])
    same = {r["amount"] for r in hits}
    assert len(same) == 1, (tax_year, section, same)
    return Decimal(first["amount"]), first["exception_ids"]


def build():
    lines = [r for r in read(D / "ordinance_lines.csv") if r["ordinance"] == "levy" and r["section"] == "levy"]
    av = read(D / "assessed_valuations.csv")
    fmap = read(ROOT / "entities" / ENT / "levy_extension_funds.csv")

    def key_of(r):
        if r["fund"].startswith("Prior Year Adjustment"):
            return "pya"
        return f"{r['fund_code']}:{r['general_fund_part']}" if r["general_fund_part"] else r["fund_code"]

    def fmap_for(key):
        for m in fmap:
            if key in m["levy_key"].split("|"):
                return m
        raise KeyError(key)

    rows = []
    for doc in LEVY_DOCS:
        fy = int(doc[2:6])
        ty = fy - 1
        valuation, _ = audit_value(av, ty, "valuation", lambda s: True)
        levy_by = defaultdict(Decimal)
        for r in lines:
            if r["doc_id"] == doc:
                levy_by[fmap_for(key_of(r))["levy_key"]] += Decimal(r["amount"])
        total_levy = total_ext = total_col = Decimal(0)
        for m in fmap:
            k = m["levy_key"]
            levy = levy_by.get(k)
            if levy is None:
                continue
            if k == "pya":
                rows.append(dict(entity_id=ENT, doc_id=doc, period_fy=fy, tax_year=ty, fund_key=k, fund=m["levy_name"], levy=levy, extension="", difference="", difference_pct="",
                                 collected="", percent_collected="", valuation=valuation, rate_computed="", rate_printed="", rate_diff="", extension_total_printed="", exception_ids="", explanation=""))
                continue
            pat = re.compile(m["audit_extension_pattern"])
            ext, _ = audit_value(av, ty, "extensions", lambda s: bool(pat.search(s)))
            col, _ = audit_value(av, ty, "collections", lambda s: bool(pat.search(s)))
            printed, _ = audit_value(av, ty, "rates", lambda s, n=m["audit_rate_name"]: s == n)
            calc = (ext / valuation * 100).quantize(Decimal("0.00001"))
            rows.append(dict(entity_id=ENT, doc_id=doc, period_fy=fy, tax_year=ty, fund_key=k, fund=m["levy_name"], levy=levy, extension=ext, difference=ext - levy,
                             difference_pct=(ext - levy) / levy * 100, collected=col, percent_collected=col / ext * 100, valuation=valuation, rate_computed=calc,
                             rate_printed=printed, rate_diff=calc - printed, extension_total_printed="", exception_ids="", explanation=""))
            total_levy += levy
            total_ext += ext
            total_col += col
        printed_total, _ = audit_value(av, ty, "extensions", lambda s: s == "Total")
        printed_col, _ = audit_value(av, ty, "collections", lambda s: s == "Total")
        printed_rate, _ = audit_value(av, ty, "rates", lambda s: s == "Total")
        pya = levy_by.get("pya", Decimal(0))
        calc = (total_ext / valuation * 100).quantize(Decimal("0.00001"))
        rows.append(dict(entity_id=ENT, doc_id=doc, period_fy=fy, tax_year=ty, fund_key="total", fund="All funds (without the Prior Year Adjustment)", levy=total_levy, extension=total_ext,
                         difference=total_ext - total_levy, difference_pct=(total_ext - total_levy) / total_levy * 100, collected=total_col,
                         percent_collected=total_col / total_ext * 100, valuation=valuation, rate_computed=calc, rate_printed=printed_rate, rate_diff=calc - printed_rate,
                         extension_total_printed=printed_total, exception_ids="", explanation=""))
    return rows


# (doc_id, fund_key) -> (exception_id, explanation)
CAP = "EX-ordinance-levy-vs-extension-cap"
DEBT = "EX-ordinance-levy-vs-extension-debt"
SHORT = "EX-ordinance-fy2022-special-rec-shortfall"
REALLOC = "EX-ordinance-fy2024-collections-reallocation"
GEN24 = "EX-ordinance-fy2024-general-extension-above-levy"
EXPLAIN = {}
for doc in ("FY2022_levy", "FY2023_levy"):
    for k in ("01", "02", "03|08", "01:imrf", "01:social_security", "01:liability", "01:audit"):
        EXPLAIN[(doc, k)] = (CAP, "Extension 1.8-3.3% below the levy ordinance: the levy asks for more than the county extended (tax cap, see the exception)")
for doc in ("FY2022_levy", "FY2023_levy", "FY2024_levy", "FY2025_levy"):
    EXPLAIN[(doc, "12")] = (DEBT, "Debt service extension is 0.9-1.0% above the levy in every year (debt is outside the tax cap)")
EXPLAIN[("FY2022_levy", "09|05")] = (SHORT, "Extension is 49,961 below the levy: the shortfall that the FY2023 and FY2024 levies ask back as a 50,000 Prior Year Adjustment")
EXPLAIN[("FY2023_levy", "pya")] = (SHORT, "Levy only: the aggregate includes it; the tax year 2022 Joint Handicap extension (303,493) equals the Special Recreation levy without it (303,488)")
EXPLAIN[("FY2024_levy", "pya")] = ("EX-ordinance-fy2024-levy-adjustment", "Levy only: this levy's aggregate leaves it out; the Special Recreation levy of 327,734 already includes it per the ordinance")
EXPLAIN[("FY2024_levy", "01")] = (GEN24, "Extension 44,767 (3.5%) above the levy while every other non-debt fund is 0.9% below; the audit's 'General & SB 508' line may carry more than the Corporate levy")
EXPLAIN[("FY2024_levy", "09|05")] = (REALLOC, "Extension matches the levy; collections are 115% of the extension while the General Fund collected 96.1% (the 2023 distribution)")
EXPLAIN[("FY2025_levy", "01:social_security")] = ("EX-ordinance-fy2025-social-security-levy", "The levy section prints 169,550; the ordinance's SUMMARY and aggregate use 173,840, 37 below the extension (173,877): the section is the typo")
EXPLAIN[("FY2024_levy", "total")] = ("EX-ordinance-fy2024-levy-adjustment", "Levy without the 50,000 Prior Year Adjustment, as the ordinance's own aggregate")
EXPLAIN[("FY2025_levy", "total")] = ("EX-ordinance-fy2025-social-security-levy", "Sum of the section levies (4,528,770); the ordinance's aggregate is 4,533,060 (Social Security at its SUMMARY figure)")
EXPLAIN[("FY2023_levy", "total")] = ("", "Fund rows sum to 4,128,719, the audit prints 4,128,720 (a dollar); the ordinance's aggregate of 4,218,185 includes the 50,000 Prior Year Adjustment")


def main():
    rows = build()
    for r in rows:
        ex = EXPLAIN.get((r["doc_id"], r["fund_key"]))
        if ex:
            r["exception_ids"], r["explanation"] = ex
        for c in ("difference_pct", "percent_collected"):
            if r[c] != "":
                r[c] = f"{r[c]:.3f}"
    with open(D / "levy_vs_extension.csv", "w", newline="") as f:
        w = csv.DictWriter(f, COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} rows -> levy_vs_extension.csv", file=sys.stderr)
    for r in rows:
        print(r["doc_id"], r["fund_key"], r["levy"], r["extension"], r["difference"], r["difference_pct"], r["percent_collected"], r["rate_computed"], r["rate_printed"], r["rate_diff"], r["extension_total_printed"])


if __name__ == "__main__":
    main()
