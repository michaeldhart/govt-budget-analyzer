"""Independent cross-check sources for the debt / pension / OPEB notes: the government-wide Statement of Net Position and the
'Reconciliation of Fund Balances of Governmental Funds to Net Position of Governmental Activities'.

read_net_position(fy, source) -> {rows:[(section, label, [values per column], page)], columns:[...], page}
read_reconciliation(fy, source) -> {rows:[(label, amount, page)], page}
"""
import re
import statistics

from audit_debt_pension_common import all_plain_text, assign_columns, page_lines, is_page_no


def find_pages(fy):
    texts = all_plain_text(fy)
    if sum(len(t) for t in texts[:5]) < 100:     # scanned (FY2012): the whole-document OCR sidecar
        from extract_audit import page_texts_for_locating
        texts = page_texts_for_locating(fy)
    np_page = rec_page = None
    for i, t in enumerate(texts[:60], 1):
        head = " ".join(t.split())[:600].upper()
        if np_page is None and "STATEMENT OF NET POSITION" in head and "PROPRIETARY" not in head and "FIDUCIARY" not in head and len(re.findall(r"\d{1,3},\d{3}", t)) > 25 \
                and "BUSINESS" in t.upper():
            np_page = i
        if rec_page is None and "RECONCILIATION OF" in head and "GOVERNMENTAL FUNDS" in head and "NET POSITION" in head and "BALANCE SHEET" not in head[:60]:
            rec_page = i
        # the reconciliation is often printed on the same page as the balance sheet (below it) or on the page after: search the text
        if rec_page is None and re.search(r"NET POSITION OF GOVERNMENTAL ACTIVITIES", t.upper()) and "RECONCILIATION" in t.upper():
            rec_page = i
    return np_page, rec_page


NP_SECTIONS = [
    (r"^ASSETS$", "assets"), (r"^DEFERRED OUTFLOWS", "deferred_outflows"), (r"^LIABILITIES$", "liabilities"), (r"^DEFERRED INFLOWS", "deferred_inflows"),
    (r"^NET POSITION$", "net_position"),
]


def read_net_position(fy, source="text", prep="gray"):
    np_page, _ = find_pages(fy)
    if np_page is None:
        return dict(rows=[], page=None)
    lines = page_lines(fy, [np_page], source, prep)[np_page]
    rows, section, pend = [], None, []
    full = []
    for ln in lines:
        label, nums = ln.split()
        if len(nums) >= 3 and re.match(r"(?i)^total assets$", label.strip()):
            full.append(ln)
    ncol = max((len(ln.split()[1]) for ln in full), default=0)
    if not ncol:
        return dict(rows=[], page=np_page)
    anchors = [statistics.median([ln.split()[1][c][0] for ln in full]) for c in range(ncol)]
    for ln in lines:
        label, nums = ln.split()
        t = ln.text.strip()
        for pat, sec in NP_SECTIONS:
            if re.match(pat, t.upper()):
                section, pend = sec, []
        if is_page_no(t):
            continue
        if nums:
            cols = assign_columns(nums, anchors, tol=22)
            vals = [cols[c][0] if c in cols else None for c in range(ncol)]
            rows.append((section, " ".join(pend + [label]).strip(), vals, ln.page))
            pend = []
        elif label and not re.match(r"(?i)^(city of belvidere|statement of net|april|primary|governmental|business|activities|total$|component|unit|ida|public|library|\(this statement)", label):
            pend.append(label)
        else:
            pend = []
    return dict(rows=rows, page=np_page, ncol=ncol)


def read_reconciliation(fy, source="text", prep="gray"):
    _, rec = find_pages(fy)
    if rec is None:
        return dict(rows=[], page=None)
    lines = page_lines(fy, [rec], source, prep)[rec]
    rows, pend, on = [], [], False
    for ln in lines:
        t = re.sub(r"\s+", " ", ln.text).strip()
        if re.search(r"(?i)amounts reported for governmental activities", t):
            on = True
            continue
        if not on:
            continue
        label, nums = ln.split()
        if is_page_no(t):
            continue
        if nums:
            rows.append((" ".join(pend + [label]).strip(), nums[-1][1], ln.page))
            pend = []
            if re.search(r"(?i)net position of governmental activities", label):
                break
        elif label and not re.match(r"(?i)^(see accompanying|city of belvidere)", label):
            pend.append(label)
    return dict(rows=rows, page=rec)


REC_IDS = [
    (r"landfill", "landfill_postclosure"), (r"installment contract", "installment_contract"), (r"debt certificates?", "debt_certificates"),
    (r"tax increment", "tif_note"), (r"capital lease", "capital_lease"), (r"revolving loan", "revolving_loan"),
    (r"compensated absences", "compensated_absences"), (r"other post-?employment|net opeb|opeb", "opeb"),
    (r"net pension (liability|obligation).*police", "npl_police"), (r"net pension (liability|obligation).*fire", "npl_fire"),
    (r"net pension (liability|obligation).*(slep)", "npl_slep"), (r"net pension (liability|obligation).*(imrf|illinois municipal)", "npl_imrf"),
    (r"net pension (liability|obligation)", "net_pension_obligation"),
]


def rec_line_ids(rows):
    """[(line_id, amount, page, label)] for the long-term liability lines of the reconciliation (amounts made positive)."""
    out = []
    after = False
    for label, amount, pg in rows:
        m = re.search(r"(?i)long-term liabilities (are )?not due and payable.*?not reported in the governmental funds\s*(.*)$|are not due and payable.*?(?:governmental funds|current period)\s*(.*)$", label)
        if m:
            after = True
            rest = (m.group(2) if m.group(2) is not None else m.group(3)) or ""
            if rest.strip():
                label = rest.strip()
            else:
                if amount:      # the pre-GASB 68 audits print one lump sum on the heading line
                    out.append(("lump_long_term_liabilities", abs(amount), pg, label))
                continue
        if re.search(r"(?i)accrued interest|net position of governmental", label):
            after = False
        if not after:
            continue
        for pat, lid in REC_IDS:
            if re.search(pat, label, re.I):
                out.append((lid, abs(amount), pg, label))
                break
    return out
