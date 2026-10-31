"""Build entities/boone_county_il/source_urls.csv from the county's Financial Information page and download the files.

Scope (decision 2026-10-31): county government only. Budgets, audits and employee compensation reports are listed;
the Circuit Clerk audits on the same page are a separate reporting unit and are not fetched.
Files go to sources/boone_county_il/<pdf_path>. size_bytes comes from the server's Content-Length; a local file
whose size differs is reported, not replaced.
Usage: python3 pipelines/boone_county_il/fetch_sources.py [--refresh-index] [--check]
"""
import csv, datetime, html, re, subprocess, sys, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "entities/boone_county_il/source_urls.csv"
SOURCES = ROOT / "sources/boone_county_il"
INDEX = "https://www.boonecountyil.gov/government/financial_information/index.php"
FIELDS = ["doc_id", "url", "link_text", "index_url", "size_bytes", "checked_on", "note", "doc_type", "fiscal_year", "status", "pdf_path"]
UA = {"User-Agent": "govt-budget-analyzer"}


def quote(url):
    p = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((p.scheme, p.netloc, urllib.parse.quote(p.path), "", ""))


def classify(link_text, path):
    """-> (doc_id, doc_type, fiscal_year, status, local path) or None for documents out of scope."""
    ext = Path(path).suffix.lower()
    if "Circuit Clerk" in path or "Circuit Clerk" in link_text:
        return None
    if "/Budgets/" in path or "Budget" in link_text:
        if "Preliminary" in link_text:
            return "FY2027_budget_prelim", "budget", 2027, "preliminary", "budgets/FY2027_budget_prelim.pdf"
        fy = int(re.search(r"FY\s*(\d{4})", link_text).group(1))
        return f"FY{fy}_budget", "budget", fy, "adopted", f"budgets/FY{fy}_budget.pdf"
    if "Financial Audit" in link_text:
        fy = int(re.search(r"(\d{4})", link_text).group(1))
        return f"FY{fy}_audit", "audit", fy, "final", f"audits/FY{fy}_audit.pdf"
    if "Compensation" in link_text:
        fy = int(re.search(r"(\d{4})", link_text).group(1))
        return f"FY{fy}_compensation", "compensation", fy, "projected", f"compensation/FY{fy}_compensation{ext}"
    return None


def refresh_index():
    page = urllib.request.urlopen(urllib.request.Request(INDEX, headers=UA), timeout=60).read().decode("utf8", "ignore")
    rows = []
    for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page, re.S):
        href = html.unescape(m.group(1))
        if not re.search(r"\.(pdf|xlsx)", href, re.I):
            continue
        text = re.sub(r"\s+", " ", html.unescape(re.sub("<.*?>", "", m.group(2)))).strip()
        text = text.replace("FY27 Preliminary Budget", "FY2027 Preliminary Budget")
        path = urllib.parse.unquote(urllib.parse.urlsplit(href).path)
        c = classify(text, path)
        if c is None:
            continue
        doc_id, doc_type, fy, status, pdf_path = c
        rows.append(dict(doc_id=doc_id, url=quote(href), link_text=text, index_url=INDEX, size_bytes="", checked_on="",
                         note="", doc_type=doc_type, fiscal_year=fy, status=status, pdf_path=pdf_path))
    return sorted(rows, key=lambda r: (r["doc_type"], r["fiscal_year"]))


def main(refresh, check_only):
    rows = refresh_index() if refresh or not MANIFEST.exists() else list(csv.DictReader(open(MANIFEST, newline="")))
    today = datetime.date.today().isoformat()
    for row in rows:
        path = SOURCES / row["pdf_path"]
        if not path.exists() and not check_only:
            path.parent.mkdir(parents=True, exist_ok=True)
            # curl follows the CMS redirect (its Location header has raw spaces, which urllib mishandles for some files)
            def get(url):
                return subprocess.run(["curl", "-sfL", "-A", UA["User-Agent"], "-o", str(path), url], timeout=300).returncode == 0
            if not get(row["url"]):
                # the CMS is case-sensitive: the page links Financial Audit 2017.PDF but only the .pdf spelling is served
                lower = re.sub(r"\.(PDF)$", ".pdf", row["url"])
                if lower == row["url"] or not get(lower):
                    sys.exit(f"cannot download {row['doc_id']}: {row['url']}")
                row["url"], row["note"] = lower, "page links the .PDF spelling, which the server refuses; the .pdf spelling is served"
        size = path.stat().st_size if path.exists() else None
        try:  # the CMS redirect sometimes refuses HEAD; the downloaded size stands in then
            req = urllib.request.Request(row["url"], method="HEAD", headers=UA)
            remote = int(urllib.request.urlopen(req, timeout=60).headers["Content-Length"])
            if size is not None and size != remote:
                print(f"SIZE MISMATCH {row['doc_id']}: local {size} server {remote}")
            size = remote
        except Exception:
            pass
        row["size_bytes"], row["checked_on"] = size, today
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} documents")


if __name__ == "__main__":
    main("--refresh-index" in sys.argv, "--check" in sys.argv)
