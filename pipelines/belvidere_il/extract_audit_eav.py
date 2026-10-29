"""Equalized assessed valuation (EAV) by tax levy year from each audit's "Assessed Valuations, Tax Rates, and Tax Extensions" schedule.

Usage: python3 pipelines/belvidere_il/extract_audit_eav.py            (every audit FY2013-FY2026; needs pdftotext)
Writes data/belvidere_il/extracted/audits/FY20XX_audit_eav.csv: levy_year, eav_city, eav_library, total_tax_rate_city_library, total_extensions, pdf_page.
Each audit prints the last five, seven or ten levy years, so the windows overlap: build_reference.py checks that every audit agrees on every
levy year it shares. The "Property Tax Levies and Collections" schedule beside it pairs each levy year with the fiscal year the tax is collected in
(levy year L -> fiscal year L+2), which is the pairing the reference table uses.
"""
import csv
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
from govbudget.entity import Entity  # noqa: E402

E = Entity.load("belvidere_il")
NUM = re.compile(r"\$?\s*(\d{1,3}(?:,\d{3})+|\d+\.\d+|\d+)")


def pages(pdf):
    text = subprocess.run(["pdftotext", "-layout", pdf, "-"], capture_output=True, text=True, check=True).stdout
    return text.split("\f")


def values(line):
    return [m.group(1) for m in NUM.finditer(line)]


def ocr_page(pdf, n):
    """FY2013-FY2015 audits: the text layer is garbled, so read the page image (300 dpi gray, tesseract psm 6)."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        png = os.path.join(d, "p.png")
        subprocess.run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=pnggray", "-r300", f"-dFirstPage={n}", f"-dLastPage={n}", f"-sOutputFile={png}", pdf], check=True)
        return subprocess.run(["tesseract", png, "-", "--psm", "6"], capture_output=True, text=True).stdout


def extract_ocr(fy):
    pdf = E.source_path("audits", f"FY{fy}_audit.pdf")
    for n, page in enumerate(pages(pdf), 1):  # find the page by its heading in the (garbled but readable) text layer; skip the table of contents
        if re.search(r"Assessed valuations, Tax Rates", page, re.I) and "Tax Rates - City" in page.replace("Tax Rates -", "Tax Rates -"):
            text = ocr_page(pdf, n)
            lines = text.split("\n")
            hdr = next((l for l in lines if len(re.findall(r"\b(?:19|20)\d\d\b", l)) >= 5 and not re.search(r"[A-Za-z]{3}", l)), None)
            if hdr is None:
                continue
            years = [int(y) for y in re.findall(r"\b((?:19|20)\d\d)\b", hdr)]
            out = {y: dict(levy_year=y, pdf_page=n) for y in years}
            city = next(l for l in lines if re.match(r"\s*City\b.*\d{3},\d{3},\d{3}", l))
            lib = next((l for l in lines if re.match(r"\s*Library\b.*\d{3},\d{3},\d{3}", l)), "")
            tot = next((l for l in lines if re.match(r"\s*Total Tax Rates", l)), "")
            for key, line, pat in (("eav_city", city, r"\d{1,3}(?:,\d{3}){2,}"), ("eav_library", lib, r"\d{1,3}(?:,\d{3}){2,}"),
                                   ("total_tax_rate_city_library", tot, r"\d\.\d{4,5}")):
                for y, v in zip(years, re.findall(pat, line)):
                    out[y][key] = int(v.replace(",", "")) if key.startswith("eav") else float(v)
            return list(out.values())
    return []


def extract(fy):
    if fy <= 2015:
        return extract_ocr(fy)
    pdf = E.source_path("audits", f"FY{fy}_audit.pdf")
    for n, page in enumerate(pages(pdf), 1):
        if "ASSESSED VALUATIONS, TAX RATES" not in page.upper() or "Tax Levy Year" not in page:
            continue
        lines = page.split("\n")
        hdr = next((l for l in lines if re.match(r"\s*Tax Levy Year\s+(\d{4})", l)), None)
        if hdr is None:
            continue
        years = [int(v) for v in values(hdr)]
        out = {y: dict(levy_year=y) for y in years}
        # the City / Library lines sit under "Assessed Valuations"; the totals are on this page or the next
        i = next(k for k, l in enumerate(lines) if l.strip() == "Assessed Valuations")
        city = next(l for l in lines[i + 1:] if re.match(r"\s*City\s+\$", l))
        lib = next((l for l in lines[i + 1:] if re.match(r"\s*Library\s+\$", l)), "")
        for y, v in zip(years, values(city.split("City", 1)[1])):
            out[y]["eav_city"] = int(v.replace(",", ""))
        for y, v in zip(years, values(lib.split("Library", 1)[1]) if lib else []):
            out[y]["eav_library"] = int(v.replace(",", ""))
        tot = next((l for l in lines if re.match(r"\s*Total Tax Rates", l)), None)
        if tot:
            for y, v in zip(years, values(tot.split("Rates", 1)[1])):
                out[y]["total_tax_rate_city_library"] = float(v)
        for y in years:
            out[y]["pdf_page"] = n
        return list(out.values())
    return []


def main():
    cols = ["fiscal_year_of_audit", "levy_year", "eav_city", "eav_library", "total_tax_rate_city_library", "pdf_page"]
    base = E.data_path("extracted", "audits")
    for fy in range(2013, 2027):
        rows = extract(fy)
        with open(os.path.join(base, f"FY{fy}_audit_eav.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for r in rows:
                w.writerow([fy, r["levy_year"], r.get("eav_city", ""), r.get("eav_library", ""), r.get("total_tax_rate_city_library", ""), r["pdf_page"]])
        print(f"FY{fy}: levy years {rows[0]['levy_year']}-{rows[-1]['levy_year']} ({len(rows)})" if rows else f"FY{fy}: no schedule found")


if __name__ == "__main__":
    main()
