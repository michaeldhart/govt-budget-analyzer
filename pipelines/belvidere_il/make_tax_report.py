"""Multi-year General Fund tax report (PDF) for the City of Belvidere, IL.

Usage: python scripts/make_tax_report.py        -> output/Belvidere_Tax_Revenue_Report.pdf

Reads only data/tax_series_by_category.csv (built by scripts/build_tax_series.py); every number and
sentence in the report is computed from it. Gross taxes, offsets and net are kept separate and fees
are excluded, per the FY2027 methodology in CLAUDE.md.

Colors: seven categorical hues from the validated reference palette (blue, orange, aqua, yellow,
magenta, green, violet; validated for light mode) in a fixed order tied to the category, plus neutral
gray for the residual "Other taxes". Every category is also labeled (legend/direct labels) and the
full numbers are in the table on the last page, as the palette's contrast warning requires.
"""
import csv
import os
import statistics
import sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, to_rgb
from matplotlib.ticker import FuncFormatter
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
from govbudget import Entity  # noqa: E402

E = Entity.load("belvidere_il")
DATA = E.data_dir
OUT = E.output_dir
CH = os.path.join(OUT, "report_charts")
os.makedirs(CH, exist_ok=True)

# ---- design tokens -------------------------------------------------------------------------
SURFACE = "#ffffff"
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID = "#e6e5e1"
_cats = sorted((c for c in E.categories("budget_category") if c["color"]), key=lambda c: int(c["sort"]))
CATS = [c["name"] for c in _cats]  # category order and colors live in entities/belvidere_il/categories.csv
COLOR = {c["name"]: c["color"] for c in _cats}
ADOPTED_GRAY, UP, DOWN, NEUTRAL = "#9a988f", "#2a78d6", "#e34948", "#f0efec"

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 8, "text.color": INK, "axes.labelcolor": INK2,
    "axes.edgecolor": GRID, "axes.linewidth": 0.8, "xtick.color": INK2, "ytick.color": INK2,
    "axes.facecolor": SURFACE, "figure.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.spines.top": False, "axes.spines.right": False,
})

# ---- data ------------------------------------------------------------------------------------
rows = list(csv.DictReader(open(os.path.join(DATA, "tax_series_by_category.csv"))))
S = {}  # (fy, measure, category) -> dict(gross, off, net)
for r in rows:
    S[(int(r["fiscal_year"]), r["measure"], r["category"])] = dict(
        gross=int(r["gross_tax"]), off=int(r["offsets"]), net=int(r["net"]))
ALL = "ALL TAXES"

# audit basis: the audit's own groups, so budget and actual are strictly comparable
TELECOM = "Telecommunications tax"
_groups = sorted(E.categories("report_group"), key=lambda c: int(c["sort"]))
GROUPS = [c["name"] for c in _groups]
GCOLOR = {c["name"]: c["color"] for c in _groups}
TELECOM_IN_TOTALS = True  # decided 2026-10-22: the municipal infrastructure maintenance line is a tax (as the audit presents it)
AB = {}
for r in csv.DictReader(open(os.path.join(DATA, "tax_series_audit_basis.csv"))):
    def iv(x):
        return int(x) if x != "" else None
    AB[(int(r["fiscal_year"]), r["group"])] = dict(orig=iv(r["budget_audit_original"]), final=iv(r["budget_audit_final"]),
                                                   actual=iv(r["actual_audited"]), docs=iv(r["budget_docs_adopted"]),
                                                   note=r["note"])
AUD_YEARS = sorted({fy for (fy, g), v in AB.items() if v["actual"] is not None})          # audited actuals: FY2012-FY2026
BUD_YEARS = sorted({fy for (fy, g), v in AB.items() if v["orig"] is not None or v["docs"] is not None})


def gbudget(fy, g):
    v = AB.get((fy, g))
    if not v:
        return None
    return v["orig"] if v["orig"] is not None else v["docs"]


def gactual(fy, g):
    v = AB.get((fy, g))
    return None if not v else v["actual"]


def gtotal(fy, kind):
    gs = [g for g in GROUPS if TELECOM_IN_TOTALS or g != TELECOM]
    vals = [(gbudget(fy, g) if kind == "budget" else gactual(fy, g)) for g in gs]
    return None if all(v is None for v in vals) else sum(v or 0 for v in vals)
AD_YEARS = list(range(2014, 2028))
ACT_YEARS = list(range(2013, 2026))
VAR_YEARS = [y for y in AD_YEARS if y in ACT_YEARS]


def get(fy, measure, cat, field="gross"):
    v = S.get((fy, measure, cat))
    return None if v is None else v[field]


def m(x, d=1):
    return f"${x/1e6:,.{d}f}M"


def pct(x, d=0):
    return f"{x*100:+.{d}f}%"


def fmt_m(v, _=None):
    return f"${v/1e6:,.0f}M" if abs(v) >= 1e6 or v == 0 else f"${v/1e3:,.0f}K"


def money_label(v, _=None):
    """$1.5M / $2M / $750K: only as many decimals as needed, so distinct ticks never share a label."""
    if v == 0:
        return "0"
    if abs(v) >= 1e6:
        return "$" + f"{v/1e6:.1f}".rstrip("0").rstrip(".") + "M"
    return f"${v/1e3:,.0f}K"


def set_money_ticks(ax, ymax, n=4):
    """Ticks on a 1/2/5 step so there are about n of them."""
    raw = ymax / n
    step = next(s_ for s_ in [5e4, 1e5, 2e5, 5e5, 1e6, 2e6, 5e6, 1e7] if s_ >= raw)
    ax.set_yticks([i * step for i in range(int(ymax / step) + 1)])
    ax.yaxis.set_major_formatter(FuncFormatter(money_label))


def style_axes(ax, y_grid=True):
    ax.tick_params(length=0)
    if y_grid:
        ax.grid(axis="y", color=GRID, linewidth=0.6)
        ax.set_axisbelow(True)
    ax.spines["left"].set_visible(False)


def save(fig, name):
    p = os.path.join(CH, name)
    fig.savefig(p, dpi=220, bbox_inches="tight")
    plt.close(fig)
    return p


# ---- chart A: composition over time (stacked columns) ----------------------------------------
def chart_composition():
    fig, ax = plt.subplots(figsize=(7.3, 3.6))
    bottom = [0] * len(AD_YEARS)
    for c in CATS:
        vals = [get(y, "budget_adopted", c) or 0 for y in AD_YEARS]
        ax.bar(AD_YEARS, vals, bottom=bottom, width=0.74, color=COLOR[c], edgecolor=SURFACE, linewidth=1.2, label=c)
        bottom = [b + v for b, v in zip(bottom, vals)]
    for y, tot in zip(AD_YEARS, bottom):
        if y in (2014, 2020, 2024, 2027):
            ax.text(y, tot + 3e5, m(tot), ha="center", va="bottom", fontsize=7.5, color=INK, fontweight="bold")
    # direct labels for the five largest FY2027 segments (smaller ones are in the legend)
    base, last = 0, 2027
    seg = []
    for c in CATS:
        v = get(last, "budget_adopted", c) or 0
        seg.append((c, base, v))
        base += v
    for c, b0, v in seg:
        if v >= 1.2e6:
            ax.text(last + 0.55, b0 + v / 2, c.replace(" taxes", "").replace(" tax", ""), va="center", fontsize=7, color=INK2)
    ax.set_xlim(2013.4, 2029.2)
    ax.set_xticks(AD_YEARS)
    ax.set_xticklabels([f"FY{str(y)[2:]}" for y in AD_YEARS], fontsize=7)
    ax.yaxis.set_major_formatter(FuncFormatter(fmt_m))
    ax.set_ylim(0, 25.5e6)
    style_axes(ax)
    h, l = ax.get_legend_handles_labels()
    fig.legend(h, l, loc="lower center", ncol=4, frameon=False, fontsize=7.5, handlelength=1.0, columnspacing=1.6,
               bbox_to_anchor=(0.5, -0.06))
    return save(fig, "a_composition.png")


# ---- chart B: small multiples, one panel per audit-basis group ----------------------------------
def chart_multiples():
    fig, axes = plt.subplots(2, 4, figsize=(7.3, 3.9))
    for ax, g in zip(axes.flat, GROUPS):
        bud = [(y, gbudget(y, g)) for y in BUD_YEARS if gbudget(y, g) is not None]
        act = [(y, gactual(y, g)) for y in AUD_YEARS if gactual(y, g) is not None]
        ax.plot(*zip(*bud), color=GCOLOR[g], linewidth=2, solid_capstyle="round")
        ax.plot(*zip(*act), linestyle="none", marker="o", markersize=3.2, markerfacecolor=SURFACE,
                markeredgecolor=INK2, markeredgewidth=0.9)
        ax.text(0, 1.2, g.replace(" (net)", ""), transform=ax.transAxes, fontsize=8, color=INK, fontweight="bold", va="bottom")
        ax.text(0, 1.04, f"FY27 budget {m(bud[-1][1], 2)}", transform=ax.transAxes, fontsize=6.8, color=INK2, va="bottom")
        ax.set_xlim(2011.3, 2028)
        ax.set_xticks([2012, 2019, 2026])
        ax.set_xticklabels(["FY12", "FY19", "FY26"], fontsize=6.5)
        top = max([v for _, v in bud + act] + [1])
        ax.set_ylim(0, top * 1.12)
        set_money_ticks(ax, top * 1.12)
        ax.tick_params(axis="y", labelsize=6.5)
        style_axes(ax)
    fig.tight_layout(h_pad=2.6, w_pad=0.8)
    fig.text(0.0, -0.02, "Line = budget; hollow dots = audited actual. Own scale per panel. Telecommunications = the municipal infrastructure "
             "maintenance line.", fontsize=7, color=INK2)
    return save(fig, "b_multiples.png")


# ---- chart C: budget vs audited actual, total and variance -----------------------------------------
def variance_series():
    out = {}
    for y in AUD_YEARS:
        a, b = gtotal(y, "budget"), gtotal(y, "actual")
        if a and b is not None:
            out[y] = (b - a) / a
    return out


def chart_budget_vs_actual():
    var = variance_series()
    fig, (ax, bx) = plt.subplots(2, 1, figsize=(7.3, 4.3), gridspec_kw=dict(height_ratios=[1.3, 1], hspace=0.5))
    bud = [(y, gtotal(y, "budget")) for y in BUD_YEARS if gtotal(y, "budget")]
    act = [(y, gtotal(y, "actual")) for y in AUD_YEARS if gtotal(y, "actual") is not None]
    ax.plot(*zip(*bud), color=ADOPTED_GRAY, linewidth=2.2, marker="s", markersize=3.5)
    ax.plot(*zip(*act), color=INK, linewidth=2.2, marker="o", markersize=3.5)
    ax.text(bud[-1][0] + 0.25, bud[-1][1], "Budget\n" + m(bud[-1][1]), va="center", fontsize=7.5, color=INK2)
    ax.text(act[-1][0] + 0.15, act[-1][1] + 1.3e6, "Audited actual " + m(act[-1][1]), va="center", ha="center", fontsize=7.5, color=INK)
    ax.set_xlim(2011.6, 2029.5)
    ax.set_xticks(range(2012, 2028, 2))
    ax.set_xticklabels([f"FY{str(y)[2:]}" for y in range(2012, 2028, 2)], fontsize=7)
    ax.yaxis.set_major_formatter(FuncFormatter(fmt_m))
    ax.set_ylim(10e6, 25e6)
    style_axes(ax)
    ax.set_title("Taxes: budget vs audited actual", loc="left", fontsize=9, fontweight="bold", color=INK)

    ys = sorted(var)
    bx.bar(ys, [var[y] * 100 for y in ys], width=0.66, color=[UP if var[y] >= 0 else DOWN for y in ys],
           edgecolor=SURFACE, linewidth=1.0)
    for y in ys:
        v = var[y] * 100
        bx.text(y, v + (1.2 if v >= 0 else -1.2), f"{v:+.0f}%", ha="center", va="bottom" if v >= 0 else "top",
                fontsize=6.8, color=INK)
    bx.axhline(0, color=INK2, linewidth=0.8)
    bx.set_xlim(2011.4, 2026.8)
    bx.set_xticks(ys)
    bx.set_xticklabels([f"FY{str(y)[2:]}" for y in ys], fontsize=6.8)
    bx.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:+.0f}%" if v else "0"))
    lo, hi = min(var.values()) * 100, max(var.values()) * 100
    bx.set_ylim(min(lo - 6, -6), hi + 8)
    style_axes(bx)
    bx.set_title("Audited actual vs budget: % above (blue) or below (red)", loc="left", fontsize=9, fontweight="bold", color=INK)
    return save(fig, "c_budget_vs_actual.png")


# ---- chart D: variance heatmap, audit group x year --------------------------------------------------
def chart_heatmap():
    cmap = LinearSegmentedColormap.from_list("div", [DOWN, NEUTRAL, UP])
    cap = 0.6
    ys = AUD_YEARS
    rows_g = [g for g in GROUPS if g != TELECOM] + [TELECOM]
    fig, ax = plt.subplots(figsize=(7.3, 3.3))
    for i, g in enumerate(rows_g):
        for j, y in enumerate(ys):
            a, b = gbudget(y, g), gactual(y, g)
            if not a or b is None:
                ax.add_patch(plt.Rectangle((j, i), 1, 1, facecolor=SURFACE, edgecolor=SURFACE, linewidth=1.5))
                ax.text(j + 0.5, i + 0.5, "-", ha="center", va="center", fontsize=7, color=MUTED)
                continue
            v = (b - a) / a
            rgb = cmap((max(-cap, min(cap, v)) + cap) / (2 * cap))
            ax.add_patch(plt.Rectangle((j, i), 1, 1, facecolor=rgb, edgecolor=SURFACE, linewidth=1.5))
            lum = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
            ax.text(j + 0.5, i + 0.5, (f"{v*100:+.0f}" if round(v * 100) else "0"), ha="center", va="center",
                    fontsize=6.2, color="#ffffff" if lum < 0.45 else INK)
    ax.set_xlim(0, len(ys))
    ax.set_ylim(len(rows_g), 0)
    ax.set_xticks([j + 0.5 for j in range(len(ys))])
    ax.set_xticklabels([f"FY{str(y)[2:]}" for y in ys], fontsize=6.8)
    ax.set_yticks([i + 0.5 for i in range(len(rows_g))])
    ax.set_yticklabels([g.replace(" (net)", "") for g in rows_g], fontsize=7.5)
    ax.xaxis.tick_top()
    ax.tick_params(length=0)
    for sp in ax.spines.values():
        sp.set_visible(False)
    fig.text(0.0, -0.03, "Blue = audited actual above budget, red = below (percent). Color saturates at 60%; a dash means no budget "
             "line that year..", fontsize=7, color=INK2)
    return save(fig, "d_heatmap.png")


# ---- chart E: offsets ----------------------------------------------------------------------------
def chart_offsets():
    cats = [c for c in CATS if any((get(y, "budget_adopted", c, "off") or 0) for y in AD_YEARS)]
    fig, ax = plt.subplots(figsize=(7.3, 2.5))
    bottom = [0] * len(AD_YEARS)
    for c in cats:
        vals = [get(y, "budget_adopted", c, "off") or 0 for y in AD_YEARS]
        ax.bar(AD_YEARS, vals, bottom=bottom, width=0.7, color=COLOR[c], edgecolor=SURFACE, linewidth=1.2, label=c)
        bottom = [b + v for b, v in zip(bottom, vals)]
    for y, t in zip(AD_YEARS, bottom):
        if y in (2014, 2020, 2026, 2027):
            ax.text(y, t - 3.5e4, f"-${abs(t)/1e3:,.0f}K", ha="center", va="top", fontsize=7, color=INK)
    ax.axhline(0, color=INK2, linewidth=0.8)
    ax.set_xticks(AD_YEARS)
    ax.set_xticklabels([f"FY{str(y)[2:]}" for y in AD_YEARS], fontsize=7)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"-${abs(v)/1e3:,.0f}K" if v else "0"))
    ax.set_ylim(min(bottom) * 1.4, 0.5e5)
    style_axes(ax)
    ax.legend(loc="lower left", frameon=False, fontsize=7.5, ncol=3, handlelength=1.0, bbox_to_anchor=(0, -0.38))
    return save(fig, "e_offsets.png")


# ---- findings (all computed) ------------------------------------------------------------------------
def findings():
    g14, g27 = get(2014, "budget_adopted", ALL), get(2027, "budget_adopted", ALL)
    n27 = get(2027, "budget_adopted", ALL, "net")
    cagr = (g27 / g14) ** (1 / 13) - 1
    peak_y = max(AD_YEARS, key=lambda y: get(y, "budget_adopted", ALL))
    peak = get(peak_y, "budget_adopted", ALL)
    delta = {c: (get(2027, "budget_adopted", c) or 0) - (get(2014, "budget_adopted", c) or 0) for c in CATS}
    top = sorted(delta, key=delta.get, reverse=True)[:3]
    share = {c: (get(2027, "budget_adopted", c) or 0) / g27 for c in CATS}
    var = variance_series()
    above = [y for y, v in var.items() if v > 0]
    med = statistics.median(var.values())
    big = max(var, key=lambda y: abs(var[y]))
    last_y = max(var)
    last_b, last_a = gtotal(last_y, "budget"), gtotal(last_y, "actual")
    util = [get(y, "budget_adopted", "Utility taxes") for y in AD_YEARS]
    return dict(g14=g14, g27=g27, n27=n27, cagr=cagr, peak_y=peak_y, peak=peak, delta=delta, top=top, share=share,
                var=var, above=above, med=med, big=big, util_peak=max(util), util_peak_y=AD_YEARS[util.index(max(util))],
                util27=get(2027, "budget_adopted", "Utility taxes"), last_y=last_y, last_b=last_b, last_a=last_a)


# ---- PDF ---------------------------------------------------------------------------------------------
ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=ss["Title"], fontName="Helvetica-Bold", fontSize=19, leading=22, alignment=0, spaceAfter=2, textColor=colors.HexColor(INK))
SUB = ParagraphStyle("SUB", parent=ss["Normal"], fontSize=9, leading=12, textColor=colors.HexColor(INK2), spaceAfter=8)
H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontName="Helvetica-Bold", fontSize=11.5, leading=14, spaceBefore=6, spaceAfter=3, textColor=colors.HexColor(INK))
BODY = ParagraphStyle("BODY", parent=ss["Normal"], fontSize=8.8, leading=12, textColor=colors.HexColor(INK))
BUL = ParagraphStyle("BUL", parent=BODY, leftIndent=11, bulletIndent=0, spaceAfter=2.5)
SMALL = ParagraphStyle("SMALL", parent=BODY, fontSize=7.4, leading=9.6, textColor=colors.HexColor(INK2))
TILE_V = ParagraphStyle("TV", parent=BODY, fontName="Helvetica-Bold", fontSize=20, leading=22)
TILE_L = ParagraphStyle("TL", parent=BODY, fontSize=7.6, leading=9.5, textColor=colors.HexColor(INK2))


def img(path, width=7.3 * inch):
    from PIL import Image as PI
    w, h = PI.open(path).size
    return Image(path, width=width, height=width * h / w)


def tiles(f):
    items = [
        (m(f["g27"], 2), "FY2027 adopted gross taxes"),
        (f"{pct(f['g27']/f['g14']-1)}", f"vs FY2014 ({m(f['g14'])}); {f['cagr']*100:.1f}% a year, not inflation-adjusted"),
        (m(f["peak"], 2), f"Peak: FY{f['peak_y']} adopted"),
        (m(f["n27"], 2), f"FY2027 net of offsets (-${(f['g27'] - f['n27'])/1e6:.2f}M)"),
    ]
    cells = [[Paragraph(v, TILE_V), ] for v, _ in items]
    t = Table([[Paragraph(v, TILE_V) for v, _ in items], [Paragraph(l, TILE_L) for _, l in items]],
              colWidths=[1.82 * inch] * 4)
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 8),
                           ("LINEBEFORE", (1, 0), (-1, -1), 0.6, colors.HexColor(GRID)),
                           ("TOPPADDING", (0, 0), (-1, 0), 2), ("BOTTOMPADDING", (0, 0), (-1, 0), 0)]))
    return t


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.HexColor(MUTED))
    canvas.drawString(0.6 * inch, 0.44 * inch, "City of Belvidere, IL - General Fund taxes. Sources: budget ordinances FY2015-27; audited reports FY2012-26 "
                      "(OCR-extracted, validated).")
    canvas.drawString(0.6 * inch, 0.32 * inch, "Gross taxes; fees excluded; not inflation-adjusted.")
    canvas.drawRightString(letter[0] - 0.6 * inch, 0.32 * inch, f"Page {doc.page}")
    canvas.restoreState()


def table_page():
    hdr = ["FY", "Property", "Muni sales", "State inc.", "Home rule", "Utility", "Replace.", "Video", "Other", "Gross", "Offsets", "Net"]
    data = [hdr]
    for y in AD_YEARS:
        row = [f"FY{y}"] + [f"{(get(y, 'budget_adopted', c) or 0):,}" if get(y, "budget_adopted", c) is not None else "-" for c in CATS]
        row += [f"{get(y, 'budget_adopted', ALL):,}", f"{get(y, 'budget_adopted', ALL, 'off'):,}", f"{get(y, 'budget_adopted', ALL, 'net'):,}"]
        data.append(row)
    t = Table(data, colWidths=[0.42 * inch] + [0.62 * inch] * 8 + [0.68 * inch, 0.55 * inch, 0.68 * inch], repeatRows=1)
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 6.6), ("FONT", (0, 1), (-1, -1), "Helvetica", 6.6),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor(INK)),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.HexColor(INK2)),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f5f2")]),
        ("FONT", (9, 1), (9, -1), "Helvetica-Bold", 6.6), ("TOPPADDING", (0, 0), (-1, -1), 2.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2),
    ]))
    return t


def audit_table():
    def block(years):
        data = [["FY", "Budget", "Actual", "Diff %"]]
        for y in years:
            b_, a_ = gtotal(y, "budget"), gtotal(y, "actual")
            data.append([f"FY{y}", f"{b_:,}" if b_ else "-", f"{a_:,}" if a_ is not None else "-",
                         f"{(a_-b_)/b_*100:+.1f}%" if (a_ is not None and b_) else "-"])
        t = Table(data, colWidths=[0.5 * inch, 1.05 * inch, 1.05 * inch, 0.6 * inch])
        t.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 6.6), ("FONT", (0, 1), (-1, -1), "Helvetica", 6.6),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.HexColor(INK2)),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f5f2")]),
            ("TOPPADDING", (0, 0), (-1, -1), 1.8), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.8)]))
        return t
    half = (len(BUD_YEARS) + 1) // 2
    outer = Table([[block(BUD_YEARS[:half]), block(BUD_YEARS[half:])]], colWidths=[3.4 * inch, 3.4 * inch], hAlign="LEFT")
    outer.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    return outer


def build():
    f = findings()
    a, b, c_, d, e = chart_composition(), chart_multiples(), chart_budget_vs_actual(), chart_heatmap(), chart_offsets()
    doc = SimpleDocTemplate(os.path.join(OUT, "Belvidere_Tax_Revenue_Report.pdf"), pagesize=letter,
                            leftMargin=0.6 * inch, rightMargin=0.6 * inch, topMargin=0.55 * inch, bottomMargin=0.65 * inch,
                            title="Belvidere, IL General Fund tax revenue, FY2014-FY2027", author="")
    top3 = ", ".join(f"{c.lower()} ({'+' if f['delta'][c]>=0 else '-'}${abs(f['delta'][c])/1e6:.1f}M)" for c in f["top"])
    story = [
        Paragraph("Belvidere, IL: General Fund tax revenue, FY2014-FY2027", H1),
        Paragraph("Adopted budgets compared across 14 fiscal years (May-April), with budget vs actual. Taxes only: fees are excluded and "
                  "offsets are shown separately.", SUB),
        tiles(f), Spacer(1, 8),
        Paragraph("What the adopted budgets show", H2),
        Paragraph(f"Gross taxes rose from {m(f['g14'])} in FY2014 to a peak of {m(f['peak'])} in FY{f['peak_y']}, then fell back to "
                  f"{m(f['g27'])} in FY2027 ({pct(f['g27']/f['g14']-1)} over the period, about {f['cagr']*100:.1f}% a year; not adjusted for inflation).", BUL, bulletText="•"),
        Paragraph(f"The largest increases FY2014 to FY2027: {top3}. In FY2027 property tax is {f['share']['Property tax']*100:.0f}% of gross taxes, "
                  f"municipal sales tax {f['share']['Municipal sales tax']*100:.0f}% and state income tax {f['share']['State income tax']*100:.0f}%.", BUL, bulletText="•"),
        Paragraph(f"Utility taxes peaked at {m(f['util_peak'])} (FY{f['util_peak_y']}) and are budgeted at {m(f['util27'])} for FY2027.", BUL, bulletText="•"),
        Paragraph(f"Audited actual taxes came in above the original budget in {len(f['above'])} of {len(f['var'])} years (FY{min(f['var'])}-FY{max(f['var'])}; "
                  f"median {pct(f['med'])}); the biggest gap was FY{f['big']} ({pct(f['var'][f['big']])}). FY{f['last_y']}, the latest audit: "
                  f"{m(f['last_a'], 2)} actual vs {m(f['last_b'], 2)} budgeted ({pct(f['last_a']/f['last_b']-1)}).", BUL, bulletText="•"),
        Spacer(1, 4), img(a),
        Paragraph("Adopted gross taxes by category (stacked; labels show the total for selected years).", SMALL),
        PageBreak(),
        Paragraph("Each tax over time", H2),
        Paragraph("Budget (line) and audited actual (dots) for each tax group, as the audits present them: sales and use taxes together, "
                  "replacement tax net of the pension distribution, and no separate home rule line. FY2012-FY2026.", SUB),
        img(b),
        Spacer(1, 6),
        Paragraph("Offsets are shown separately", H2),
        Paragraph("Offsets reduce what the city keeps: replacement tax distributed to pensions, and sales tax and property tax rebated to "
                  "developers. They are not netted into the category totals above.", SUB),
        img(e),
        PageBreak(),
        Paragraph("Budget vs actual", H2),
        Paragraph("Actuals are the audited figures from the City's annual financial reports (General Fund schedule of revenues, FY2012-FY2026). "
                  "Budget is the original budget in the same schedule (FY2027: the adopted ordinance). This is the audit's basis, so its totals differ from "
                  "page 1: replacement tax is net of the pension distribution, sales and use taxes are combined, and local motor fuel tax is left out.", SUB),
        img(c_),
        Spacer(1, 6),
        Paragraph("Where the budget missed, by tax", H2),
        Paragraph("Percent difference between audited actual and budget for each tax group and year (blank where a tax had no budget line).", SUB),
        img(d),
        PageBreak(),
        Paragraph("Notes and data quality", H2),
    ]
    notes = [
        "<b>Budgets.</b> City of Belvidere budget ordinances FY2015-FY2027 (General Fund, Exhibit A). The PDFs are scanned images, read by OCR "
        "and checked line by line: every department total and the fund total in each year ties to the printed totals (the city's own figures differ "
        "from its line items by a few dollars in places). Corrections are recorded in <i>data/overrides/</i>.",
        "<b>Adopted budget</b> is the figure in each year's own ordinance. The audits' original and final budgets equal it on 134 of 135 comparable "
        "line-years, which confirms it. (The following year's document shows different prior-year numbers for FY2024 utility taxes and the FY2025 "
        "Audit and IMRF levies; the audit shows those later figures are not what was adopted.)",
        "<b>Actuals</b> are audited, from the General Fund schedule of revenues in the annual financial reports FY2012-FY2026, extracted and "
        "checked to each section's printed total. They replace the 'Actual' columns in the budget documents, which are preliminary and in some years "
        "carry budget figures for the replacement-tax pension distribution. The audit does not split municipal and home rule sales tax, so "
        "budget-vs-actual is shown by the audit's groups.",
        "<b>Scope.</b> Taxes in the General Fund only. Pension levies in the police and fire funds, motor fuel tax fund, TIF, SSA and capital "
        "fund property taxes are excluded to avoid double counting. Franchise fees are fees. The municipal infrastructure maintenance line "
        "(the audits' 'Telecommunications tax', $62K-$197K a year) is counted as a tax: inside Other taxes on page 1, and as its own group on "
        "the audit-basis pages. Other taxes on page 1 = hotel/motel, auto rental, local use, local motor fuel, cannabis and that line. Local motor "
        "fuel tax is left out of the audit-basis charts in every year, "
        "because the audits report it inside Grants in FY2020-FY2025 (and separately only in FY2026).",
        "<b>Not adjusted for inflation</b> and not per-capita. FY = May 1 to April 30 (FY2027 = May 2026 to April 2027).",
        "<b>Limits.</b> There is no audited actual for FY2027 yet. Budget-vs-actual percentages for small groups can look large on small "
        "dollar amounts.",
    ]
    story += [Paragraph(n, BUL, bulletText="•") for n in notes]
    story += [Spacer(1, 8), Paragraph("Data table: adopted gross taxes by category (dollars)", H2),
              Paragraph("Same numbers as the charts. 'Gross' is the sum of the eight categories; 'Net' = gross + offsets.", SUB),
              table_page(),
              Spacer(1, 10), Paragraph("Data table: budget vs audited actual by tax group (dollars)", H2),
              Paragraph("Audit basis (local motor fuel tax excluded). Budget = original budget in the audit; FY2027 = adopted ordinance.", SUB),
              audit_table()]
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    print("wrote", os.path.relpath(os.path.join(OUT, "Belvidere_Tax_Revenue_Report.pdf")))


if __name__ == "__main__":
    build()
