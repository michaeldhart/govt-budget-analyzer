"""Download the park district's documents listed in entities/belvidere_park_district/source_urls.csv.

Files go to sources/belvidere_park_district/<pdf_path>. size_bytes and checked_on are filled from
the server's Content-Length (HEAD request); a file whose local size differs is reported, not replaced.
Usage: python3 pipelines/belvidere_park_district/fetch_sources.py [--check]
"""
import csv, datetime, hashlib, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "entities/belvidere_park_district/source_urls.csv"
SOURCES = ROOT / "sources/belvidere_park_district"


def remote_size(url):
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "govt-budget-analyzer"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return int(r.headers["Content-Length"])


def main(check_only):
    rows = list(csv.DictReader(open(MANIFEST, newline="")))
    today = datetime.date.today().isoformat()
    for row in rows:
        path = SOURCES / row["pdf_path"]
        size = remote_size(row["url"])
        if not path.exists() and not check_only:
            path.parent.mkdir(parents=True, exist_ok=True)
            req = urllib.request.Request(row["url"], headers={"User-Agent": "govt-budget-analyzer"})
            path.write_bytes(urllib.request.urlopen(req, timeout=300).read())
        if path.exists() and path.stat().st_size != size:
            print(f"SIZE MISMATCH {row['doc_id']}: local {path.stat().st_size} server {size}")
        row["size_bytes"], row["checked_on"] = size, today
    with open(MANIFEST, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} documents")


if __name__ == "__main__":
    main("--check" in sys.argv)
