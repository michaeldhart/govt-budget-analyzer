"""Describe each downloaded park district document: pages, text layer or scan, sha256.

Writes data/belvidere_park_district/source_inventory.csv from entities/belvidere_park_district/source_urls.csv
and the PDFs under sources/belvidere_park_district/. A page counts as text when it has about 800+
characters on average; below 50 it is a scan (needs OCR).
"""
import csv, hashlib, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "sources/belvidere_park_district"
OUT = ROOT / "data/belvidere_park_district/source_inventory.csv"


def main():
    rows = list(csv.DictReader(open(ROOT / "entities/belvidere_park_district/source_urls.csv", newline="")))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["doc_id", "doc_type", "fiscal_year", "status", "pages", "chars_per_page", "layer", "sha256", "size_bytes"])
        for r in rows:
            p = SRC / r["pdf_path"]
            info = subprocess.run(["pdfinfo", str(p)], capture_output=True, text=True, check=True).stdout
            pages = int(next(l.split()[1] for l in info.splitlines() if l.startswith("Pages")))
            txt = subprocess.run(["pdftotext", "-layout", str(p), "-"], capture_output=True, text=True, check=True).stdout
            per = len(txt.strip()) / pages
            layer = "text" if per > 800 else "partial" if per > 50 else "scan"
            w.writerow([r["doc_id"], r["doc_type"], r["fiscal_year"], r["status"], pages, round(per),
                        layer, hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_size])


if __name__ == "__main__":
    main()
