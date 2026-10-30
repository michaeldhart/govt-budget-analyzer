"""WORK IN PROGRESS (no main yet, writes nothing): reader for the "Budget Report by Fund - All" (layout C: the adopted FY2024 budget).

Status 2026-10-30: whole-page OCR of this scan is wrecked by dotted rules (only about half the lines read cleanly in any one reading). `READINGS`
runs six OCR readings (gray and thr150 at 150, 200 and 300 dpi) and `consensus` keeps, per account, a reading whose Budgeted and Remaining Balance
columns agree; 294 lines get one, 71 do not, and several fund totals still overshoot their printed totals (duplicate keys from misread account
numbers). Not validated; see docs/PARK_DISTRICT_PLAN.md.


Usage: python3 pipelines/belvidere_park_district/extract_budget_fund_report.py FY2024

Layout: `Fund: NN Name`, then `Expenditure` lines and `Revenue` lines (`FF-DDDD-OOO  label  Budgeted  Current Year To Date  Encumbrance  Remaining
Balance  PCT`), `<Fund> Total Revenue` and `<Fund> Total Expenditure` per fund and `Report Total Revenue` / `Report Total Expenditure` / `Report Totals
Net` at the end. Nothing was spent when it was printed (year to date and encumbrance are 0), so Remaining Balance equals Budgeted; when OCR loses the
Budgeted cell (underline marks run across the row) the Remaining Balance gives it and the line is flagged `budget_from_remaining`. Scanned, with heavy
scan noise on every line; read through the shared OCR cache (gray, then thr150 as a second reading).
Output (data/belvidere_park_district/extracted/<doc_id>_{revenue,expense,totals,corrections}.csv): the long format of layout A, amount column `budgeted`.
Validation: per fund, lines = Total Revenue and = Total Expenditure; the funds sum to the Report totals; Report Totals Net = revenue - expenditure.
A total OCR cannot read, or a line it garbles, is resolved the way the other layouts do (single-digit confusion, the thr150 reading) or by an override
with a reason.
"""
import csv
import os
import re
import sys
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import extract_budget_report as R  # noqa: E402
import extract_budget_operating as A  # noqa: E402
import extract_budget_version as V  # noqa: E402  fund_sum, crossread, KNOWN_FUNDS

ENTITY = R.ENTITY
ACCT = re.compile(r"^\W*([0-9OQoIlSs]{1,3})-{1,2}(\d{3,4})-{1,2}(\d{3})(?!\d)\s*(.*)$")
FUND_HEAD = re.compile(r"^\W*Fund:\s*(\d{2})\b")
TOTAL = re.compile(r"^\W*(.*?)\s*Total\s+(Revenue|Expenditure)\b\W*(.*)$")
REPORT = re.compile(r"^\W*Report\s+Totals?\s+(Revenue|Expenditure|Net)\b\W*(.*)$")
READINGS = [("gray", 150), ("gray", 200), ("gray", 300), ("thr150", 150), ("thr150", 200), ("thr150", 300)]
MONEY = re.compile(r"(?<![\d,])\$?(\d{1,3}(?:,\d{3})*[.,]\d{2})(?!\d)")


def money_tokens(text):
    return [Decimal(m.group(1).replace(",", "", m.group(1).count(",") - (1 if m.group(1)[-3] == "," else 0)).replace(",", ".")) for m in MONEY.finditer(text)]


def agree(toks):
    """Budgeted == Remaining Balance (the first and the last amount) with only zeros between: two columns read independently that agree."""
    return len(toks) >= 2 and toks[0] == toks[-1] and all(t == 0 for t in toks[1:-1]) and toks[0] != 0 or (len(toks) >= 2 and all(t == 0 for t in toks))


def read_pages(pages, fy, doc, overrides=(), corrections=None, used=None):
    """Lines and printed totals of one OCR reading, with the fund and section heading in force and whether the line's two amount columns agree."""
    rows, totals = [], []
    used = used if used is not None else set()
    fund, flow = None, "expense"
    for pdf_page, page in enumerate(pages, start=1):
        label = f"pdf{pdf_page}"
        for raw in page.splitlines():
            line = raw
            for i, o in enumerate(overrides):
                if o["kind"] in ("line", "line_prefix") and re.sub(r"^\W+", "", line).startswith(o["raw"]):
                    if corrections is not None:
                        corrections.append((doc, label, pdf_page, o["kind"], line.strip(), o["fixed"], o["reason"]))
                    line = o["fixed"] if o["kind"] == "line" else o["fixed"] + re.sub(r"^\W+", "", line)[len(o["raw"]):]
                    used.add(i)
            s = V.norm(line.strip())
            if not s:
                continue
            fh = FUND_HEAD.match(s)
            if fh:
                fund = fh.group(1)
                continue
            if re.fullmatch(r"\W*Expenditure\W*", s):
                flow = "expense"
                continue
            if re.fullmatch(r"\W*Revenue\W*", s):
                flow = "revenue"
                continue
            rp = REPORT.match(s)
            t = None if rp else TOTAL.match(s)
            if rp or t:
                toks = money_tokens((rp.group(2) if rp else t.group(3)))
                name = "Report" if rp else t.group(1).strip()
                kind = {"Revenue": "revenue", "Expenditure": "expense", "Net": "net"}[(rp or t).group(1 if rp else 2)]
                totals.append((label, pdf_page, name, kind, toks[0] if toks and agree(toks) else None, toks))
                continue
            a = ACCT.match(s)
            if a and fund:
                text_part = a.group(4)
                first = MONEY.search(text_part)
                text = re.sub(r"[^\w &/.,'()-]", " ", text_part[:first.start()] if first else text_part)
                text = re.sub(r"\s+(?:\S*[~=—_«“”]\S*)+.*$", "", text).strip(" -_.,~")
                toks = money_tokens(text_part)
                fcode = a.group(1) if a.group(1) in V.KNOWN_FUNDS else fund
                flags = ["account_prefix_repaired"] if fcode != a.group(1) else []
                rows.append(dict(fiscal_year=fy, doc=doc, page_label=label, pdf_page=pdf_page, fund_code=fcode, fund="", dept_code=a.group(2),
                                 account=f"{fcode}-{a.group(2)}-{a.group(3)}", line_item=text, flow=flow, column="budgeted",
                                 amount=R.clean(toks[0]) if toks else None, flag=";".join(flags), section="", tag="", agree=agree(toks) and bool(toks)))
    return rows, totals


def consensus(readings):
    """One row per (account, flow) from several OCR readings: a reading whose two amount columns agree wins; several agreeing readings must give the
    same amount (otherwise the line is left with `reading_conflict` and a total decides)."""
    best = {}
    for ri, (rows, _) in enumerate(readings):
        for r in rows:
            k = (r["account"], r["flow"])
            best.setdefault(k, []).append((ri, r))
    out = []
    for k, items in best.items():
        good = [(ri, r) for ri, r in items if r["agree"] and r["amount"] is not None]
        if good:
            amounts = {Decimal(str(r["amount"])) for _, r in good}
            r = dict(good[0][1])
            if len(amounts) > 1:
                r["flag"] = ";".join(filter(None, [r["flag"], "reading_conflict"]))
            out.append(r)
        else:
            r = dict(items[0][1])
            r["flag"] = ";".join(filter(None, [r["flag"], "no_agreeing_reading"]))
            r["alts"] = sorted({Decimal(str(x["amount"])) for _, x in items if x["amount"] is not None})
            out.append(r)
    return out
