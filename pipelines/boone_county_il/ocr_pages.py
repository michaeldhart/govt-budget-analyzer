"""OCR a scanned Boone County budget book into layout text (one file per page) that extract_budget_book.py reads like pdftotext -layout output.

Usage: python3 pipelines/boone_county_il/ocr_pages.py FY2018_budget [FY2023_budget ...] [--workers N] [--pages 1-5] [--second]
--second makes the independent second reading (450 dpi, --psm 4) into data/boone_county_il/ocr/<doc>_r2/: extract_budget_book.py compares the two readings line by line
and takes the second wherever that makes a failing printed total tie (the repair ladder of the scanned FY2018 book).
Pages are rendered at 300 dpi (grayscale) and read by tesseract (--psm 6, TSV words); each text line is rebuilt with spaces so that a word starts at the
character column of its left edge (column = left / median character width), which keeps the figures of a table in their columns.  The cache is
data/boone_county_il/ocr/<doc>/pNNN.txt (gitignored, regenerable).
"""
import csv, io, os, re, statistics, subprocess, sys, tempfile
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "sources/boone_county_il/budgets"
CACHE = ROOT / "data/boone_county_il/ocr"


def layout_text(tsv):
    rows = list(csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE))
    words = [r for r in rows if r["level"] == "5" and r["text"].strip() and float(r["conf"]) >= 0]
    if not words:
        return ""
    cw = statistics.median(int(w["width"]) / max(1, len(w["text"])) for w in words if len(w["text"]) >= 3) or 10
    lines = {}
    for w in words:
        lines.setdefault((w["block_num"], w["par_num"], w["line_num"]), []).append(w)
    out = []
    for ws in sorted(lines.values(), key=lambda ws: (min(int(w["top"]) for w in ws), min(int(w["left"]) for w in ws))):
        ws.sort(key=lambda w: int(w["left"]))
        s = ""
        for w in ws:
            col = int(round(int(w["left"]) / cw))
            s += " " * max(1 if s else 0, col - len(s)) + w["text"]
        out.append((min(int(w["top"]) for w in ws), s))
    # merge fragments of one visual line (tesseract splits a wide table row into several 'lines' at nearly the same height)
    merged = []
    for top, s in out:
        if merged and abs(top - merged[-1][0]) <= 8:
            a = merged[-1][1]
            merged[-1] = (merged[-1][0], a if len(a) >= len(s) and s.strip() in a else (a + " " * max(1, len(s) - len(s.lstrip()) - len(a)) + s.lstrip() if len(s) - len(s.lstrip()) > len(a) else s[:len(s) - len(s.lstrip())] + s.lstrip() + " " + a.lstrip()))
        else:
            merged.append((top, s))
    return "\n".join(s for _, s in merged)


def ocr_page(args):
    doc, p = args[0], args[1]
    second = len(args) > 2 and args[2]
    out = CACHE / (doc + "_r2" if second else doc) / f"p{p:03d}.txt"
    if out.exists():
        return p
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        base = os.path.join(tmp, "pg")
        subprocess.run(["pdftoppm", "-r", "450" if second else "300", "-f", str(p), "-l", str(p), "-gray", "-png", "-singlefile", str(SRC / f"{doc}.pdf"), base], check=True)
        tsv = subprocess.run(["tesseract", base + ".png", "-", "--psm", "4" if second else "6", "tsv"], capture_output=True, text=True).stdout
    out.write_text(layout_text(tsv))
    return p


def n_pages(doc):
    info = subprocess.run(["pdfinfo", str(SRC / f"{doc}.pdf")], capture_output=True, text=True, check=True).stdout
    return int(re.search(r"Pages:\s+(\d+)", info).group(1))


def main(argv):
    workers, pages, second = 4, None, False
    docs = []
    i = 0
    while i < len(argv):
        if argv[i] == "--workers":
            workers = int(argv[i + 1]); i += 2
        elif argv[i] == "--second":
            second = True; i += 1
        elif argv[i] == "--pages":
            a, b = argv[i + 1].split("-"); pages = range(int(a), int(b) + 1); i += 2
        else:
            docs.append(argv[i]); i += 1
    jobs = [(d, p, second) for d in docs for p in (pages or range(1, n_pages(d) + 1))]
    with Pool(workers) as pool:
        for k, _ in enumerate(pool.imap_unordered(ocr_page, jobs), 1):
            if k % 20 == 0:
                print(f"{k}/{len(jobs)} pages", flush=True)
    print(f"{len(jobs)} pages in {CACHE}")


if __name__ == "__main__":
    main(sys.argv[1:])
