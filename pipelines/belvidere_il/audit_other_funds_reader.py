"""Positional page reader for extract_audit_other_funds.py.

Two sources of words with bounding boxes:
  * text layer (FY2016-FY2026, and FY2013-15 where usable): pdfplumber words. The audit PDFs' text layer inserts a junk
    space glyph that overlaps the first digit of many numbers ("6 83,730"); such space characters are dropped before
    words are built, so the number comes out whole.
  * OCR (FY2012-FY2015): tesseract TSV on a 300 dpi render (same render as extract_budget.ocr_page).

A page becomes a list of Line(top, words); a word is (x0, x1, text). Numeric tokens are then assigned to table columns
by their RIGHT edge, which resolves cells the audit leaves blank (the old text-only approach had to guess them).
"""
import os
import re
import subprocess
import sys
from collections import namedtuple

import pdfplumber
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from extract_audit import DATA_E, audit_pdf, to_int  # noqa: E402

Word = namedtuple("Word", "x0 x1 text")
Line = namedtuple("Line", "top words")


def _lines_from_words(words, ytol=3.2):
    """words: list of (x0, x1, top, text) -> Lines sorted top to bottom, words left to right."""
    words = sorted(words, key=lambda w: (w[2], w[0]))
    lines = []
    for x0, x1, top, text in words:
        if lines and abs(lines[-1][0] - top) <= ytol:
            lines[-1][1].append(Word(x0, x1, text))
            lines[-1][0] = (lines[-1][0] * (len(lines[-1][1]) - 1) + top) / len(lines[-1][1])
        else:
            lines.append([top, [Word(x0, x1, text)]])
    return [Line(t, sorted(ws, key=lambda w: w.x0)) for t, ws in lines]


def text_layer_lines(fy, page):
    with pdfplumber.open(audit_pdf(fy)) as d:
        p = d.pages[page - 1]
        chars = p.chars
        solid = [c for c in chars if c["text"].strip()]

        def junk(c):  # a space glyph sitting on top of a visible character
            if c["text"].strip():
                return False
            for s in solid:
                if abs(s["top"] - c["top"]) < 3 and s["x0"] < c["x1"] - 0.3 and s["x1"] > c["x0"] + 0.3:
                    ov = min(s["x1"], c["x1"]) - max(s["x0"], c["x0"])
                    if ov > 0.5 * (c["x1"] - c["x0"]):
                        return True
            return False

        drop = {id(c) for c in chars if not c["text"].strip() and junk(c)}
        q = p.filter(lambda o: o.get("object_type") != "char" or id(o) not in drop)
        words = [(w["x0"], w["x1"], w["top"], w["text"]) for w in q.extract_words(x_tolerance=2.2, keep_blank_chars=False)]
    return _lines_from_words(words)


def ocr_lines(fy, page, prep="gray", cache=True):
    """Tesseract TSV words (300 dpi pixel coordinates / 300 * 72 to points)."""
    outdir = os.path.join(DATA_E, "ocr", "audit_pages", f"FY{fy}_{prep}_tsv")
    os.makedirs(outdir, exist_ok=True)
    tsv = os.path.join(outdir, f"p{page:03d}.tsv")
    if not (cache and os.path.exists(tsv)):
        pgm = os.path.join(outdir, f"p{page:03d}.pgm")
        png = pgm.replace(".pgm", ".png")
        subprocess.run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=pgmraw", "-r300", f"-dFirstPage={page}",
                        f"-dLastPage={page}", f"-sOutputFile={pgm}", audit_pdf(fy)], check=True)
        im = Image.open(pgm)
        if prep.startswith("thr"):
            t = int(prep[3:])
            im = im.point(lambda v: 255 if v > t else 0)
        im.save(png)
        if fy == 2012:  # FY2012's combining statements are landscape tables printed sideways on a portrait page: ask tesseract's OSD
            osd = subprocess.run(["tesseract", png, "-", "--psm", "0"], capture_output=True, text=True)
            m = re.search(r"Rotate: (\d+)", osd.stdout + osd.stderr)
            if m and int(m.group(1)) in (90, 180, 270):
                im.rotate(-int(m.group(1)), expand=True, fillcolor=255).save(png)
        out = subprocess.run(["tesseract", png, "-", "--psm", "6", "tsv"], capture_output=True, text=True).stdout
        os.remove(pgm)
        os.remove(png)
        open(tsv, "w").write(out)
    words = []
    for ln in open(tsv).read().splitlines()[1:]:
        f = ln.split("\t")
        if len(f) < 12 or f[0] != "5" or not f[11].strip():
            continue
        l, t, w, hh = int(f[6]), int(f[7]), int(f[8]), int(f[9])
        t = t + hh // 2  # vertical centre: dashes and ruled-line fragments sit at different tops than the digits of their row
        s = 72 / 300
        words.append((l * s, (l + w) * s, t * s, f[11].strip()))
    return _lines_from_words(words, ytol=4.6)


def page_lines(fy, page, source):
    return text_layer_lines(fy, page) if source == "text" else ocr_lines(fy, page, source)


# ---- numbers -------------------------------------------------------------------------------------
NUM = re.compile(r"^\(?\s*[\d][\d,\.]*\)?$|^\(?-\)?$|^[-—–]$")


OCR_DIGITS = str.maketrans({"A": "4", "O": "0", "o": "0", "l": "1", "I": "1", "i": "1", "S": "5", "s": "5", "B": "8", "g": "9", "Z": "2", "z": "2", "t": "1", "r": "1", "b": "6", "e": "6"})


def tok_value(text, ocr=False):
    """int for a numeric token ('1,234', '(1,234)', '-'), else None. Dashes are 0."""
    t = text.strip()
    if t in ("-", "—", "–", "=", "_"):
        return 0
    if t in ("$", "(", ")", "|"):
        return None
    if re.fullmatch(r"\(?\$?[\d,\.]*\d\)?", t) or re.fullmatch(r"\(?-\)?", t):
        if re.fullmatch(r"\(?-\)?", t):
            return 0
        neg = t.startswith("(")
        digits = re.sub(r"[^\d]", "", t)
        # a number with a decimal point is not a table amount (percentages, years in headings are handled by callers)
        return -int(digits) if neg else int(digits)
    if ocr and re.fullmatch(r"\(?[\dA-Za-z,.]{3,}\)?", t) and len(re.findall(r"\d", t)) >= 2 and len(re.findall(r"[A-Za-z]", t)) <= 1:
        u = t.translate(OCR_DIGITS)  # one letter standing for a digit ("A85" = 485)
        neg = u.startswith("(")
        digits = re.sub(r"[^\d]", "", u)
        if len(digits) == len(re.sub(r"[^\dA-Za-z]", "", t)):
            return -int(digits) if neg else int(digits)
    return None


LABEL_LEADERS = {"MAY", "APRIL", "#", "SSA", "TIF", "NO.", "FUND"}


def split_line(line, ocr=False):
    """-> (label, [(x1_adjusted, value)]) ; value tokens are the right-hand numeric words, label the rest."""
    ws = [w for w in line.words if w.text not in ("$", "|", "!") and not re.fullmatch(r"[$§S]{1,3}", w.text)]
    if ocr:  # OCR scatters junk marks (quotes, vertical-rule fragments, stray letters) between and after the numbers of a row
        isnum = [tok_value(w.text) is not None for w in ws]
        keep = []
        for i, w in enumerate(ws):
            short = len(w.text) <= 2 and not re.search(r"\d", w.text) and w.text not in ("(", ")", "-", "—", "–")
            if short and any(isnum[:i]) and (any(isnum[i + 1:]) or i == len(ws) - 1):
                continue
            keep.append(w)
        ws = keep
        # trailing scan artifacts ("10D") after the last formatted amount of a data row are not label text
        j = max((i for i, w in enumerate(ws) if w.x0 > 230 and re.fullmatch(r"\(?\d{1,3}(,\d{3})+\)?", w.text)), default=None)
        if j is not None:
            ws = ws[:j + 1] + [w for w in ws[j + 1:] if tok_value(w.text, True) is not None or w.text in ("(", ")")]
    # join a lone "(" with the number after it and a number with a following lone ")"
    merged = []
    for w in ws:
        if merged and merged[-1].text == "(" and re.match(r"^[\d]", w.text):
            merged[-1] = Word(merged[-1].x0, w.x1, "(" + w.text)
        elif w.text == ")" and merged and re.match(r"^\(?[\d,]+$", merged[-1].text):
            merged[-1] = Word(merged[-1].x0, w.x1, merged[-1].text + ")")
        else:
            merged.append(w)
    vals = []
    while merged:
        w = merged[-1]
        v = tok_value(w.text, ocr)
        if v is None:
            break
        x1 = w.x1 - (3.4 if w.text.endswith(")") else 0.0)
        vals.append((x1, v))
        merged.pop()
    # a number that is part of the label ("FUND BALANCE, MAY 1", "FARMINGTON SSA #2") is not a cell
    while vals and merged and merged[-1].text.upper().strip(",") in LABEL_LEADERS:
        w = merged[-1]
        merged.append(Word(w.x1, w.x1, str(vals.pop()[1])))
        break
    label = " ".join(w.text for w in merged).strip(" :.")
    return label, list(reversed(vals))
