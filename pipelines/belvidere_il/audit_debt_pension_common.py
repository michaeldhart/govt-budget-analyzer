"""Shared helpers for the audit long-term debt / pension / OPEB extractors (extract_audit_debt_pension.py).

Pages are read as *positioned words* so that tables with blank cells keep their columns:
  * text layer (FY2013-FY2026): `pdftotext -bbox` word boxes;
  * scanned / poor text layer (FY2012-FY2015): tesseract TSV word boxes at 300 dpi (psm 6), cached under
    data/belvidere_il/ocr/audit_pages/FY20XX_dp_tsv/.
Words are clustered into lines (same baseline), each line keeps its words with x0/x1 so numbers can be assigned to
columns by their right edge.  Nothing here knows about a particular table; the table readers live in
audit_debt_pension_debt.py / audit_debt_pension_pension.py.
"""
import csv
import html
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from extract_audit import DATA_E, ROOT, audit_pdf  # noqa: E402

OVERRIDE_DIR = os.path.join(DATA_E, "overrides")
OUT_DIR = os.path.join(DATA_E, "extracted", "audits")
ENTITY_ID = "belvidere_il"


class Word:
    __slots__ = ("x0", "x1", "y0", "y1", "t")

    def __init__(self, x0, x1, y0, y1, t):
        self.x0, self.x1, self.y0, self.y1, self.t = x0, x1, y0, y1, t

    def __repr__(self):
        return f"{self.t}@{self.x1:.0f}"


class Line:
    """One printed line: words left to right, with helpers to split label from trailing numbers."""

    def __init__(self, words, page):
        self.words = sorted(words, key=lambda w: w.x0)
        self.page = page
        self.y = sum((w.y0 + w.y1) / 2 for w in self.words) / len(self.words)
        self.text = " ".join(w.t for w in self.words)

    def tokens(self):
        """[(x1, kind, value, raw)] for every word, kind in {'num','dash','pct','word','cur'}."""
        out = []
        for w in self.words:
            kind, val = classify(w.t)
            out.append((w.x1, kind, val, w.t, w.x0))
        return out

    def split(self):
        """(label, [(x1, value, kind, raw)]) where the numbers are the maximal trailing run of numeric / dash / pct tokens
        ($ signs inside the run are ignored)."""
        toks = self.tokens()
        end = len(toks)
        i = end
        while i > 0 and toks[i - 1][1] in ("num", "dash", "pct", "cur"):
            i -= 1
        nums = [(x1, v, k, raw) for (x1, k, v, raw, _x0) in toks[i:] if k != "cur"]
        label = " ".join(t[3] for t in toks[:i])
        return label.strip(), nums

    def __repr__(self):
        return f"p{self.page}: {self.text}"


NUM_RE = re.compile(r"^\(?-?\$?\(?\d[\d,]*(\.\d+)?\)?$")
PCT_RE = re.compile(r"^\(?-?\d[\d,]*(\.\d+)?(%\)?|\)%)$")
DASH_RE = re.compile(r"^[-–—]+$")

PAGE_NO = r"-{1,2}\s*\d{1,3}\s*-{0,2}|\d{1,3}\s*-{1,2}"


def is_page_no(text):
    """'- 30 -', '-- 30', '35 --' or both numbers on one line ('- 39 44 -'): a printed page footer."""
    toks = text.split()
    if not toks or len(toks) > 5:
        return False
    ints = [t for t in toks if t.isdigit()]
    dashes = [t for t in toks if re.fullmatch(r"-{1,2}", t)]
    if len(ints) + len(dashes) != len(toks) or not ints or not dashes or len(ints) > 2 or any(len(t) > 3 for t in ints):
        return False
    return toks[0] in ("-", "--") or toks[-1] in ("-", "--") or re.fullmatch(r"-{1,2}\d{1,3}", toks[0]) is not None
OCR_FIX = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "|": "1", "S": "5", "s": "5", "B": "8", "g": "9", "r": "1", "t": "1", "Z": "2"})


def classify(raw):
    """Classify a word: ('num', int|float), ('pct', float), ('dash', 0), ('cur', None) for '$', else ('word', None)."""
    t = raw.strip()
    if t in ("$", "§", "S$", "$-"):
        return ("cur", None) if t != "$-" else ("dash", 0)
    if DASH_RE.match(t):
        return "dash", 0
    t2 = t.replace("{", "(").replace("}", ")")
    if PCT_RE.match(t2):
        neg = "(" in t2 or t2.startswith("-")
        v = float(re.sub(r"[^\d.]", "", t2))
        return "pct", -v if neg else v
    if NUM_RE.match(t2):
        neg = "(" in t2 or t2.startswith("-")
        digits = re.sub(r"[^\d.]", "", t2)
        if "." in digits:
            return "num", -float(digits) if neg else float(digits)
        v = int(digits)
        return "num", -v if neg else v
    # "$1,234" glued
    if t2.startswith("$") and NUM_RE.match(t2[1:]):
        return classify(t2[1:])
    return "word", None


def ocr_number_fix(raw):
    """Repair an OCR'd numeric token: letters read for digits ("r,003,s29"), stray trailing marks.  Returns the repaired token
    only when it then parses as a number, else the original."""
    t = raw.strip().strip("|;:")
    if not re.search(r"\d", t):
        return raw
    if re.fullmatch(r"[\(\$\-]*[0-9OolI|SsBgrtZ,.]{2,}[\)%]?", t) and sum(c.isdigit() for c in t) >= 1:
        fixed = "".join(c.translate(OCR_FIX) if c.isalpha() or c == "|" else c for c in t)
        if classify(fixed)[0] in ("num", "pct"):
            return fixed
    return raw


# ---- reading pages ------------------------------------------------------------------------------
def n_pages(fy):
    return int(subprocess.run(["qpdf", "--show-npages", audit_pdf(fy)], capture_output=True, text=True).stdout.strip())


_BBOX_CACHE = {}


def text_words(fy, first, last):
    """{page: [Word]} from the text layer (pdftotext -bbox)."""
    key = (fy, first, last)
    if key in _BBOX_CACHE:
        return _BBOX_CACHE[key]
    out = subprocess.run(["pdftotext", "-bbox", "-f", str(first), "-l", str(last), audit_pdf(fy), "-"], capture_output=True, text=True).stdout
    pages, cur, pg = {}, None, first - 1
    for m in re.finditer(r'<page [^>]*>|<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', out):
        if m.group(0).startswith("<page"):
            pg += 1
            cur = pages.setdefault(pg, [])
        else:
            t = html.unescape(m.group(5))
            cur.append(Word(float(m.group(1)), float(m.group(3)), float(m.group(2)), float(m.group(4)), t))
    _BBOX_CACHE[key] = pages
    return pages


def ocr_words(fy, page, prep="gray"):
    """[Word] from tesseract TSV on a 300 dpi render (cached)."""
    from PIL import Image
    d = os.path.join(DATA_E, "ocr", "audit_pages", f"FY{fy}_dp_tsv_{prep}")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"p{page:03d}.tsv")
    if not os.path.exists(path):
        pgm = os.path.join(d, f"p{page:03d}.pgm")
        png = pgm.replace(".pgm", ".png")
        subprocess.run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=pgmraw", "-r300", f"-dFirstPage={page}", f"-dLastPage={page}",
                        f"-sOutputFile={pgm}", audit_pdf(fy)], check=True)
        im = Image.open(pgm)
        if prep.startswith("thr"):
            t = int(prep[3:])
            im = im.point(lambda v: 255 if v > t else 0)
        im.save(png)
        tsv = subprocess.run(["tesseract", png, "-", "--psm", "6", "tsv"], capture_output=True, text=True).stdout
        open(path, "w").write(tsv)
        os.remove(pgm)
        os.remove(png)
    words = []
    for r in csv.reader(open(path), delimiter="\t", quoting=csv.QUOTE_NONE):
        if len(r) < 12 or r[0] == "level" or r[0] != "5":
            continue
        txt = r[11].strip()
        if not txt:
            continue
        l, t, w, h = int(r[6]), int(r[7]), int(r[8]), int(r[9])
        words.append(Word(l / 300 * 72, (l + w) / 300 * 72, t / 300 * 72, (t + h) / 300 * 72, txt))
    return words


def cluster_lines(words, page, ytol=None):
    """Group words into lines by vertical centre."""
    ws = sorted(words, key=lambda w: (w.y0 + w.y1) / 2)
    lines, cur, cy = [], [], None
    for w in ws:
        c = (w.y0 + w.y1) / 2
        h = max(w.y1 - w.y0, 4)
        tol = ytol if ytol is not None else h * 0.55
        if cur and abs(c - cy) <= tol:
            cur.append(w)
            cy = sum((x.y0 + x.y1) / 2 for x in cur) / len(cur)
        else:
            if cur:
                lines.append(Line(cur, page))
            cur, cy = [w], c
    if cur:
        lines.append(Line(cur, page))
    return lines


def page_lines(fy, pages, source="text", prep="gray"):
    """{page: [Line]} for the requested pages from the text layer or OCR."""
    out = {}
    if source == "text":
        lo, hi = min(pages), max(pages)
        allw = text_words(fy, lo, hi)
        for p in pages:
            out[p] = cluster_lines(allw.get(p, []), p)
    else:
        for p in pages:
            ws = ocr_words(fy, p, prep)
            for w in ws:
                w.t = w.t
            out[p] = cluster_lines(ws, p)
    return out


def page_plain_text(fy, first, last):
    """pdftotext -layout page texts (for locating only)."""
    out = subprocess.run(["pdftotext", "-layout", "-f", str(first), "-l", str(last), audit_pdf(fy), "-"], capture_output=True, text=True).stdout
    return out.split("\f")


_PLAIN_CACHE = {}


def all_plain_text(fy):
    if fy not in _PLAIN_CACHE:
        t = subprocess.run(["pdftotext", "-layout", audit_pdf(fy), "-"], capture_output=True, text=True).stdout.split("\f")
        if t and not t[-1].strip():
            t = t[:-1]
        _PLAIN_CACHE[fy] = t
    return _PLAIN_CACHE[fy]


# ---- column assignment --------------------------------------------------------------------------
def assign_columns(nums, anchors, tol=None):
    """nums: [(x1, value, kind, raw)]; anchors: sorted list of column right edges.  Returns dict col_index -> (value, kind, raw).
    Each number goes to the nearest anchor (no two numbers to one anchor); numbers farther than tol from any anchor are dropped."""
    if not anchors:
        return {}
    if tol is None:
        gaps = [b - a for a, b in zip(anchors, anchors[1:])]
        tol = (min(gaps) if gaps else 60) * 0.6
    res = {}
    for x1, v, k, raw in nums:
        j = min(range(len(anchors)), key=lambda i: abs(anchors[i] - x1))
        if abs(anchors[j] - x1) > tol:
            continue
        if j in res:
            # two numbers in one column: keep the right-most (the other is a label fragment)
            if x1 < res[j][3]:
                continue
        res[j] = (v, k, raw, x1)
    return {j: v[:3] for j, v in res.items()}


# ---- overrides ----------------------------------------------------------------------------------
def load_overrides(fy):
    """data/belvidere_il/overrides/audit_debt_pension_FY20XX.csv: kind,key,value,reason.  Returns list of dict."""
    path = os.path.join(OVERRIDE_DIR, f"audit_debt_pension_FY{fy}.csv")
    if not os.path.exists(path):
        return []
    return list(csv.DictReader(open(path)))


def money(v):
    return f"{v:,.0f}" if isinstance(v, (int, float)) else str(v)


class Checker:
    """Collects validation problems.  `allow` maps a check key to the printed-minus-computed difference that the overrides file accepts
    (the source's own arithmetic slips); accepted differences are listed in `.accepted`, never silently dropped."""

    def __init__(self, allow=None):
        self.allow = dict(allow or {})
        self.problems = []
        self.accepted = []
        self.used = set()
        self.n_pass = {}      # table prefix of the check key -> checks that passed outright
        self.n_fail = {}

    def _ok(self, key):
        t = key.split("|")[0]
        self.n_pass[t] = self.n_pass.get(t, 0) + 1

    def _bad(self, key):
        t = key.split("|")[0]
        self.n_fail[t] = self.n_fail.get(t, 0) + 1

    def eq(self, key, printed, computed, desc=""):
        if printed == computed:
            self._ok(key)
            return True
        d = printed - computed
        if self.allow.get(key) == d:
            self.used.add(key)
            self.accepted.append(dict(key=key, desc=desc, printed=printed, computed=computed, diff=d))
            return True
        self._bad(key)
        self.problems.append(f"[{key}] {desc}: printed {money(printed)} vs computed {money(computed)} (diff {d:,.2f})".replace(".00)", ")"))
        return False

    def near(self, key, printed, computed, tol, desc=""):
        if abs(printed - computed) <= tol:
            self._ok(key)
            return True
        d = round(printed - computed, 2)
        if self.allow.get(key) == d:
            self.used.add(key)
            self.accepted.append(dict(key=key, desc=desc, printed=printed, computed=computed, diff=d))
            return True
        self._bad(key)
        self.problems.append(f"[{key}] {desc}: printed {printed} vs computed {computed:.2f} (tol {tol})")
        return False

    def fail(self, key, msg):
        if key in self.allow:
            self.used.add(key)
            self.accepted.append(dict(key=key, desc=msg, printed="", computed="", diff=""))
            return
        self._bad(key)
        self.problems.append(f"[{key}] {msg}")


def load_allow(fy):
    """overrides kind `allow`: key, value (diff), reason -> {key: diff}.  Other kinds are returned separately."""
    allow, other = {}, []
    for o in load_overrides(fy):
        if o["kind"] == "allow":
            allow[o["key"]] = float(o["value"]) if "." in o["value"] else int(o["value"])
        else:
            other.append(o)
    return allow, other
